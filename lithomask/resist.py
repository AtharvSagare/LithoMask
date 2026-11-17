"""Phase 4 - resist models, sub-pixel contour extraction and CD / edge-placement measurement.

Conventions
  * A resist model maps an aerial image I (clear field = 1) to a pair (F, level): the printed ("bright", exposed) region is
    {F >= level}; its boundary is the resist contour. Dark features (chrome lines that remain) are the complement.
  * Arrays are indexed [iy, ix]; pixel centres at Grid.xs / Grid.ys (nm). All lengths nm, slopes per nm unless stated.
  * Models: ConstantThreshold (CTR), VariableThreshold (VTR: threshold = f(Imax, Imin, slope) over a local window),
    CompactResist (sum of terms c_i * M_i, [Feng24 Eq.4-6]).
  * Contours: marching squares (contourpy, linear sub-pixel interpolation); regions touching the grid edge are closed along
    the grid boundary.
TODO-VERIFY (see notes/formulas.md): exact placement of the neutralisation constant b and the Gauss-Laguerre kernel
normalisation of the compact model in [Feng24]; both are implemented as documented below and are fully parameterised.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from contourpy import FillType, contour_generator
from scipy import ndimage, signal
from shapely.geometry import Polygon as _SPoly
from shapely.geometry import box as _box

from .geometry import Polygon
from .optics import Grid, Imager


# ======================================================================================
# Kernels and operators
# ======================================================================================
def gauss_laguerre_kernel(sigma_nm: float, pixel: float, order: int = 0) -> np.ndarray:
    """Radial kernel G_{s,p}(r) = L_s(r^2 / (2 p^2)) exp(-r^2 / (2 p^2)), s = `order` (0 -> plain Gaussian), p = diffusion
    length `sigma_nm`. Truncated at (4 + s) p. Normalised to unit sum of |G| for s >= 1 (the plain Gaussian sums to 1;
    higher orders integrate to ~0 so unit-sum is impossible) -- normalisation TODO-VERIFY against [Feng24]."""
    p = sigma_nm / pixel
    rad = max(1, int(math.ceil((4 + order) * p)))
    y, x = np.mgrid[-rad:rad + 1, -rad:rad + 1]
    u = (x ** 2 + y ** 2) / (2 * p * p)
    from numpy.polynomial import laguerre
    c = np.zeros(order + 1)
    c[order] = 1.0
    G = laguerre.lagval(u, c) * np.exp(-u)
    return G / (G.sum() if order == 0 else np.abs(G).sum())


def convolve_kernel(A: np.ndarray, sigma_nm: float, pixel: float, order: int = 0) -> np.ndarray:
    """A (x) G_{order, sigma}; reflect boundary."""
    if order == 0:
        return ndimage.gaussian_filter(A, sigma_nm / pixel, mode="reflect", truncate=4.0)
    K = gauss_laguerre_kernel(sigma_nm, pixel, order)
    r = K.shape[0] // 2
    P = np.pad(A, r, mode="reflect")
    return signal.fftconvolve(P, K, mode="valid")


def derivative(I: np.ndarray, pixel: float, k: int) -> np.ndarray:
    """k = 0: I ; k = 1: |grad I| (1/nm) ; k = 2: Laplacian of I (1/nm^2)."""
    if k == 0:
        return I
    if k == 1:
        gy, gx = np.gradient(I, pixel)
        return np.hypot(gx, gy)
    if k == 2:
        return ndimage.laplace(I, mode="nearest") / pixel ** 2
    raise ValueError("derivative order k must be 0, 1 or 2")


# ======================================================================================
# Resist models
# ======================================================================================
class ResistModel:
    """Interface: field(I, pixel) -> (F, level); parameter vector with bounds for the Phase 6 optimiser."""

    def field(self, I: np.ndarray, pixel: float) -> Tuple[np.ndarray, float]:
        raise NotImplementedError

    def param_names(self) -> List[str]:
        return []

    def get_vector(self) -> np.ndarray:
        return np.zeros(0)

    def set_vector(self, v: Sequence[float]) -> None:
        pass

    def bounds(self) -> List[Tuple[float, float]]:
        return []


@dataclass
class ConstantThreshold(ResistModel):
    """Printed where I >= threshold."""
    threshold: float = 0.3

    def field(self, I, pixel):
        return I, self.threshold

    def param_names(self):
        return ["threshold"]

    def get_vector(self):
        return np.array([self.threshold])

    def set_vector(self, v):
        self.threshold = float(v[0])

    def bounds(self):
        return [(0.02, 0.98)]


@dataclass
class VariableThreshold(ResistModel):
    """T(x) = t0 + k_max*Imax + k_min*Imin + k_slope*S ; printed where I >= T.
    Imax/Imin: max/min of I over a disk of radius `window_nm`; S = Gaussian-smoothed |grad I| in units of 1/(100 nm)."""
    t0: float = 0.3
    k_max: float = 0.0
    k_min: float = 0.0
    k_slope: float = 0.0
    window_nm: float = 150.0
    slope_sigma_nm: float = 20.0

    def threshold_map(self, I, pixel):
        r = max(1, int(round(self.window_nm / pixel)))
        y, x = np.mgrid[-r:r + 1, -r:r + 1]
        fp = (x ** 2 + y ** 2) <= r * r
        Imax = ndimage.maximum_filter(I, footprint=fp, mode="nearest")
        Imin = ndimage.minimum_filter(I, footprint=fp, mode="nearest")
        S = ndimage.gaussian_filter(derivative(I, pixel, 1), self.slope_sigma_nm / pixel, mode="nearest") * 100.0
        return self.t0 + self.k_max * Imax + self.k_min * Imin + self.k_slope * S

    def field(self, I, pixel):
        return I - self.threshold_map(I, pixel), 0.0

    def param_names(self):
        return ["t0", "k_max", "k_min", "k_slope"]

    def get_vector(self):
        return np.array([self.t0, self.k_max, self.k_min, self.k_slope])

    def set_vector(self, v):
        self.t0, self.k_max, self.k_min, self.k_slope = (float(a) for a in v)

    def bounds(self):
        return [(0.02, 0.98), (-1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0)]


@dataclass
class Term:
    """One compact-model term  c * M,  M = { [ D_k(I) + b ]^n (x) G_{s, sigma} }^(1/n)   ([Feng24 Eq.4-5], b sign: TODO-VERIFY).
    k: derivative order (0 I, 1 |grad I|, 2 Laplacian). n: exponent (1 = linear). s: Gauss-Laguerre order. sigma: nm.
    `fit` lists which of ('c', 'b', 'sigma') the optimiser may vary."""
    c: float = 1.0
    k: int = 0
    b: float = 0.0
    n: float = 1.0
    s: int = 0
    sigma: float = 0.0           # nm; 0 -> no convolution
    fit: Tuple[str, ...] = ("c",)

    def evaluate(self, I: np.ndarray, pixel: float) -> np.ndarray:
        base = derivative(I, pixel, self.k) + self.b
        if self.n != 1.0:
            base = np.power(base, self.n) if float(self.n).is_integer() else np.power(np.maximum(base, 0.0), self.n)
        y = convolve_kernel(base, self.sigma, pixel, self.s) if self.sigma > 0 else base
        if self.n != 1.0:
            y = np.sign(y) * np.abs(y) ** (1.0 / self.n)
        return self.c * y


@dataclass
class CompactResist(ResistModel):
    """R = sum_i c_i M_i ; printed where R >= threshold. Typical: identity + a few blurred / gradient / curvature terms."""
    terms: List[Term] = field(default_factory=lambda: [Term(1.0)])
    threshold: float = 0.3

    def field(self, I, pixel):
        R = np.zeros_like(I)
        for t in self.terms:
            R = R + t.evaluate(I, pixel)
        return R, self.threshold

    # ---- parameter vector (threshold + the fitted entries of every term)
    _BOUNDS = {"c": (-5.0, 5.0), "b": (-1.0, 1.0), "sigma": (2.0, 300.0)}

    def _slots(self):
        return [(i, a) for i, t in enumerate(self.terms) for a in t.fit]

    def param_names(self):
        return ["threshold"] + [f"t{i}.{a}" for i, a in self._slots()]

    def get_vector(self):
        return np.array([self.threshold] + [getattr(self.terms[i], a) for i, a in self._slots()], dtype=float)

    def set_vector(self, v):
        self.threshold = float(v[0])
        for (i, a), x in zip(self._slots(), v[1:]):
            setattr(self.terms[i], a, float(x))

    def bounds(self):
        return [(0.02, 0.98)] + [self._BOUNDS[a] for _, a in self._slots()]

    @staticmethod
    def example_8term(threshold: float = 0.3) -> "CompactResist":
        """Structure of [Feng24 Eq.6]; coefficients are placeholders for calibration (Phase 6), not fitted values."""
        T = [Term(1.0, 0, 0.0, 1, 0, 0.0, ("c",)),
             Term(0.0, 0, 0.1, 1, 0, 30.0, ("c", "b", "sigma")),
             Term(0.0, 0, -0.1, 1, 0, 60.0, ("c", "b", "sigma")),
             Term(0.0, 1, 0.0, 1, 0, 30.0, ("c", "sigma")),
             Term(0.0, 0, 0.0, 2, 0, 40.0, ("c", "sigma")),
             Term(0.0, 2, 0.0, 1, 0, 30.0, ("c", "sigma"))]
        return CompactResist(T, threshold)


# ======================================================================================
# Contour extraction
# ======================================================================================
def extract_contours(F: np.ndarray, grid: Grid, level: float, region: str = "bright", layer: int = 1,
                     datatype: int = 0, min_area: float = 0.0) -> List[Polygon]:
    """Closed polygons (nm) bounding {F >= level} (region='bright') or {F < level} ('dark'). Holes are returned as holes;
    regions touching the grid border are clipped to the grid extent."""
    Z = np.asarray(F, float)
    lv = level
    if region == "dark":
        Z, lv = -Z, -level
    elif region != "bright":
        raise ValueError("region must be 'bright' or 'dark'")
    low = Z.min() - 1.0 - abs(lv)
    # two pad layers: edge-replicated (so a region touching the border continues past it) then a low sentinel (so it closes
    # outside the grid); the result is clipped to the grid extent below -> no inward shift of border-touching regions.
    Zp = np.pad(np.pad(Z, 1, mode="edge"), 1, mode="constant", constant_values=low)
    pe = grid.pixel
    xs = np.concatenate([[grid.x0 - 1.5 * pe, grid.x0 - 0.5 * pe], grid.xs, [grid.x0 + (grid.nx + 0.5) * pe, grid.x0 + (grid.nx + 1.5) * pe]])
    ys = np.concatenate([[grid.y0 - 1.5 * pe, grid.y0 - 0.5 * pe], grid.ys, [grid.y0 + (grid.ny + 0.5) * pe, grid.y0 + (grid.ny + 1.5) * pe]])
    gen = contour_generator(xs, ys, Zp, fill_type=FillType.OuterOffset)
    pts_list, off_list = gen.filled(lv, float(Zp.max()) + 1.0)
    x0, x1, y0, y1 = grid.extent
    clip = _box(x0, y0, x1, y1)
    out: List[Polygon] = []
    for pts, off in zip(pts_list, off_list):
        rings = [pts[a:b - 1] for a, b in zip(off[:-1], off[1:]) if b - a >= 4]       # drop the repeated closing point
        if not rings:
            continue
        sp = _SPoly(rings[0], rings[1:])
        if not sp.is_valid:
            sp = sp.buffer(0)
        clipped = sp.intersection(clip)
        geoms = getattr(clipped, "geoms", [clipped])
        for gm in geoms:
            if gm.is_empty or gm.geom_type != "Polygon" or gm.area <= min_area:
                continue
            out.append(Polygon(np.asarray(gm.exterior.coords)[:-1], [np.asarray(h.coords)[:-1] for h in gm.interiors],
                               layer, datatype))
    return out


# ======================================================================================
# Measurements (sub-pixel)
# ======================================================================================
def sample_line(F: np.ndarray, grid: Grid, p0, p1, step: Optional[float] = None, order: int = 3):
    """Sample F along p0 -> p1 (nm). Returns (s, values), s = distance from p0 in nm."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    L = float(np.hypot(*(p1 - p0)))
    step = step or grid.pixel / 8.0
    n = max(2, int(math.ceil(L / step)) + 1)
    s = np.linspace(0.0, L, n)
    pts = p0[None, :] + (p1 - p0)[None, :] * (s / L)[:, None]
    ix = (pts[:, 0] - grid.x0) / grid.pixel - 0.5
    iy = (pts[:, 1] - grid.y0) / grid.pixel - 0.5
    v = ndimage.map_coordinates(np.asarray(F, float), [iy, ix], order=order, mode="nearest")
    return s, v


def crossings(s: np.ndarray, v: np.ndarray, level: float) -> np.ndarray:
    """Positions (same unit as s) where v crosses `level`, by linear interpolation between samples."""
    d = v - level
    idx = np.flatnonzero((d[:-1] * d[1:] < 0) | ((d[:-1] == 0) & (d[1:] != 0)))
    t = d[idx] / (d[idx] - d[idx + 1])
    return s[idx] + t * (s[idx + 1] - s[idx])


def measure_cd(F: np.ndarray, grid: Grid, p0, p1, level: float, feature: str = "bright") -> float:
    """Width (nm) of the feature (bright: F > level; dark: F < level) that contains the midpoint of the cutline p0 -> p1.
    NaN if the midpoint is not inside such a feature or a bounding edge lies outside the cutline."""
    s, v = sample_line(F, grid, p0, p1)
    mid = 0.5 * s[-1]
    vm = float(np.interp(mid, s, v))
    if (feature == "bright") != (vm > level):
        return float("nan")
    c = crossings(s, v, level)
    left, right = c[c < mid], c[c > mid]
    if len(left) == 0 or len(right) == 0:
        return float("nan")
    return float(right.min() - left.max())


def edge_placement(F: np.ndarray, grid: Grid, point, direction, level: float, search_nm: float = 200.0) -> float:
    """Signed distance (nm) along unit `direction` from `point` to the nearest crossing of `level` within +-search_nm
    (EP gauge; EPE = EP - intended offset). NaN if none."""
    d = np.asarray(direction, float)
    d = d / np.hypot(*d)
    pt = np.asarray(point, float)
    s, v = sample_line(F, grid, pt - search_nm * d, pt + search_nm * d)
    c = crossings(s, v, level) - search_nm
    return float(c[np.argmin(np.abs(c))]) if len(c) else float("nan")


# ======================================================================================
# End-to-end
# ======================================================================================
@dataclass
class PrintResult:
    aerial: np.ndarray
    field: np.ndarray
    level: float
    grid: Grid
    contours: List[Polygon]


def simulate_print(polys: Sequence[Polygon], imager: Imager, resist: ResistModel, grid: Grid, tone: str = "clear_field",
                   region: Optional[str] = None, boundary: str = "padded", **imaging_kw) -> PrintResult:
    """mask polygons -> aerial image -> resist field -> contour polygons.
    `region` defaults to what stays on the wafer for a positive resist: 'dark' for clear_field masks (chrome lines print as
    unexposed resist), 'bright' for dark_field masks (openings / contact holes)."""
    I = imager.image(polys, grid, tone=tone, boundary=boundary, **imaging_kw)
    F, lv = resist.field(I, grid.pixel)
    region = region or ("dark" if tone == "clear_field" else "bright")
    return PrintResult(I, F, lv, grid, extract_contours(F, grid, lv, region))
