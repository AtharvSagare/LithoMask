"""Stage C - inverse (pixel-based) OPC [Poonawala-Milanfar 2007; Ma-Arce 2011 structure, TODO-VERIFY constants].

Variables: theta (per pixel), mask coverage m = (1 + cos theta) / 2 in [0, 1] (m = 1 where the mask has a polygon, same
convention as the rasteriser); transmission t = bg + (fg - bg) * m  (clear_field: bg 1, fg 0; dark_field: bg 0, fg 1).
Forward (SOCS, [Feng24 Eq.3], exact same kernels as Imager):   I = sum_i lam_i |ifft(fft(t_pad) * Phi_i)|^2.
Resist: constant threshold, printed-feature indicator z = sigmoid(s * (thr - I)) (clear field: feature is dark) or
        sigmoid(s * (I - thr)) (dark field). z target d = coverage of the target polygons (printed shapes).
Cost:   J = sum W (z - d)^2 + gamma * sum m (1 - m) + tv * sum sqrt(|grad m|^2 + eps^2).
Gradient (adjoint, exact):  dJ/dt = 2 Re ifft( sum_i lam_i conj(Phi_i) * fft( G * a_i ) ),  G = dJ/dI,  a_i = field of kernel i.
It is verified against central finite differences in tests/test_opc_inverse.py.
Optimiser: Adam on theta (steepest-descent family; learning rate = InverseOptions.step rad). gamma ramps x10 over the run so the
mask ends nearly binary; the result is thresholded at m = 0.5 and converted to polygons (sub-pixel contours, or Manhattan pixel
runs when InverseOptions.manhattan). Pieces that do not touch the target are returned as assist features.
Only ConstantThreshold resists are differentiated here (the compact / variable models would need their own adjoint).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage
from shapely.geometry import box as _box
from shapely.ops import unary_union

from ..geometry import Polygon
from ..optics import Grid, rasterize_coverage
from ..resist import ConstantThreshold, extract_contours
from .common import (InverseOptions, OPCOptions, ProcessModel, epe_stats, measure_epe)
from .fragments import fragment_polygons

TV_EPS = 1e-3


def _sigmoid(x):
    return 0.5 * (1.0 + np.tanh(0.5 * x))


class InverseProblem:
    """Differentiable forward model + cost for pixel-based OPC on a fixed grid."""

    def __init__(self, target: Sequence[Polygon], pm: ProcessModel, opts: InverseOptions, grid: Grid,
                 extra: Sequence[Polygon] = ()):
        if not isinstance(pm.resist, ConstantThreshold):
            raise ValueError("inverse OPC currently needs a ConstantThreshold resist model")
        self.pm, self.o, self.grid = pm, opts, grid
        self.thr = float(pm.resist.threshold)
        self.K = pm.imager.kernels(grid.pixel)
        self.pad = self.K.nk // 2
        self.fg, self.bg = (0.0, 1.0) if pm.tone == "clear_field" else (1.0, 0.0)
        self.sign = 1.0 if pm.tone == "clear_field" else -1.0          # printed feature: sign * (thr - I) > 0
        self.d = rasterize_coverage(list(target), grid, pm.supersample)
        self.e = rasterize_coverage(list(extra), grid, pm.supersample) if len(extra) else np.zeros(grid.shape)
        self.W = np.ones(grid.shape)
        self._shape_pad = tuple(n + 2 * self.pad for n in grid.shape)
        self.PH = self.K.freq_kernels(self._shape_pad)

    # ---------------------------------------------------------------- forward
    def effective(self, m: np.ndarray) -> np.ndarray:
        return m * (1.0 - self.e) + self.e                              # fixed assist features always on

    def forward(self, m: np.ndarray):
        meff = self.effective(m)
        t = self.bg + (self.fg - self.bg) * meff
        T = np.pad(t, self.pad, mode="constant", constant_values=self.bg)
        F = np.fft.fft2(T)
        A = np.fft.ifft2(F[None] * self.PH, axes=(1, 2))
        I = np.tensordot(self.K.lams, np.abs(A) ** 2, axes=(0, 0))
        p = self.pad
        return I[p:p + self.grid.ny, p:p + self.grid.nx], A

    # ---------------------------------------------------------------- cost / gradient
    def cost_grad(self, theta: np.ndarray, gamma: float = 0.0, tv: float = 0.0):
        m = 0.5 * (1.0 + np.cos(theta))
        I, A = self.forward(m)
        s = self.o.steepness
        z = _sigmoid(s * self.sign * (self.thr - I))
        r = z - self.d
        J_fit = float(np.sum(self.W * r * r))
        G = 2.0 * self.W * r * z * (1.0 - z) * (-s * self.sign)         # dJ/dI
        Gp = np.zeros(self._shape_pad)
        p = self.pad
        Gp[p:p + self.grid.ny, p:p + self.grid.nx] = G
        S = np.fft.ifft2(np.conj(self.PH) * np.fft.fft2(Gp[None] * A, axes=(1, 2)), axes=(1, 2))
        gT = 2.0 * np.tensordot(self.K.lams, S.real, axes=(0, 0))[p:p + self.grid.ny, p:p + self.grid.nx]
        gm = gT * (self.fg - self.bg) * (1.0 - self.e)
        J = J_fit
        if gamma:
            J += gamma * float(np.sum(m * (1.0 - m)))
            gm = gm + gamma * (1.0 - 2.0 * m)
        if tv:
            Jt, gt = tv_cost_grad(m)
            J += tv * Jt
            gm = gm + tv * gt
        return J, gm * (-0.5 * np.sin(theta)), I, m


def tv_cost_grad(m: np.ndarray, eps: float = TV_EPS):
    """Smoothed isotropic total variation of m (forward differences) and its gradient."""
    dx = np.zeros_like(m)
    dy = np.zeros_like(m)
    dx[:, :-1] = np.diff(m, axis=1)
    dy[:-1, :] = np.diff(m, axis=0)
    r = np.sqrt(dx * dx + dy * dy + eps * eps)
    px, py = dx / r, dy / r
    g = np.zeros_like(m)
    g[:, :-1] -= px[:, :-1]
    g[:, 1:] += px[:, :-1]
    g[:-1, :] -= py[:-1, :]
    g[1:, :] += py[:-1, :]
    return float(np.sum(r)), g


# ======================================================================================
# Driver
# ======================================================================================
def inverse_opc(target: Sequence[Polygon], pm: ProcessModel, opts: OPCOptions, extra_mask: Sequence[Polygon] = ()):
    """Returns (main polygons, assist polygons, log, epe_before, epe_after)."""
    io = opts.inverse
    layer, dtype = target[0].layer, target[0].datatype
    grid = Grid.from_bbox(_bbox(list(target) + list(extra_mask)), io.pixel, pm.margin)
    prob = InverseProblem(target, pm, io, grid, extra_mask)
    fl = fragment_polygons(target, opts.fragments)
    pts, nrm = fl.midpoints(), fl.normals()
    cov0 = prob.d
    if io.init == "blurred":
        cov0 = ndimage.gaussian_filter(cov0, 0.5 * pm.imager.wavelength / pm.imager.na / grid.pixel * 0.35)
    m0 = np.clip(0.04 + 0.92 * cov0, 0.02, 0.98)
    theta = np.arccos(2.0 * m0 - 1.0)
    mom1, mom2 = np.zeros_like(theta), np.zeros_like(theta)
    b1, b2, eps = 0.9, 0.999, 1e-8
    log: List[dict] = []
    before = None
    for it in range(io.iterations + 1):
        gamma = io.gamma_binary * (1.0 + 9.0 * (it / max(1, io.iterations)) ** 2)
        J, g, I, m = prob.cost_grad(theta, gamma, io.tv)
        F_ = I
        epe = measure_epe(F_, prob.thr, grid, pts, nrm, pm.inside_feature, opts.model.search)
        st = epe_stats(epe)
        log.append({"stage": "inverse", "iter": it, "rms": st["rms"], "max": st["max"], "cost": float(J)})
        if opts.progress is not None and opts.progress("inverse", it, st["rms"], st["max"]) is False:
            break
        if it == io.iterations:
            break
        mom1 = b1 * mom1 + (1 - b1) * g
        mom2 = b2 * mom2 + (1 - b2) * g * g
        theta = theta - io.step * (mom1 / (1 - b1 ** (it + 1))) / (np.sqrt(mom2 / (1 - b2 ** (it + 1))) + eps)
    m_final = 0.5 * (1.0 + np.cos(theta))
    main, assists = binarize_to_polygons(m_final, grid, target, io, layer, dtype)
    from .engine import measure_print_epe          # local import: engine imports this module
    before = measure_print_epe(target, list(target) + list(extra_mask), pm, opts)
    after = measure_print_epe(target, main + assists + list(extra_mask), pm, opts)
    return main, assists, log, before, after


def _bbox(polys):
    from ..geometry import bbox_of
    return bbox_of(polys)


def binarize_to_polygons(m: np.ndarray, grid: Grid, target: Sequence[Polygon], io: InverseOptions, layer: int,
                         dtype: int) -> Tuple[List[Polygon], List[Polygon]]:
    """m >= 0.5 -> polygons; features/gaps narrower than io.min_feature are removed; pieces touching the target are 'main',
    the others are assists."""
    if io.manhattan:
        B = m >= 0.5
        boxes = []
        pix = grid.pixel
        for iy in range(grid.ny):
            row = B[iy]
            if not row.any():
                continue
            d = np.diff(np.concatenate([[0], row.astype(int), [0]]))
            for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
                boxes.append(_box(grid.x0 + s * pix, grid.y0 + iy * pix, grid.x0 + e * pix, grid.y0 + (iy + 1) * pix))
        G = unary_union(boxes) if boxes else None
    else:
        polys = extract_contours(m, grid, 0.5, "bright", layer, dtype)
        G = unary_union([p.to_shapely() for p in polys]) if polys else None
    if G is None or G.is_empty:
        return [], []
    h = io.min_feature / 2.0
    if h > 0:
        G = G.buffer(-h, join_style=2).buffer(h, join_style=2)          # drop pieces narrower than min_feature
        G = G.buffer(h, join_style=2).buffer(-h, join_style=2)          # fill gaps / holes smaller than min_feature
    if not io.manhattan and io.simplify > 0:
        G = G.simplify(io.simplify, preserve_topology=True)
    if G.is_empty:
        return [], []
    T = unary_union([p.to_shapely() for p in target])
    geoms = list(getattr(G, "geoms", [G]))
    main, assists = [], []
    for gm in geoms:
        if gm.is_empty or gm.geom_type != "Polygon":
            continue
        (main if gm.intersects(T) else assists).extend(Polygon.from_shapely(gm, layer, dtype))
    return main, assists
