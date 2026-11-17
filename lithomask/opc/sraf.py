"""Stage D - rule-based sub-resolution assist features (SRAF) with a printability check.

Placement (nm): for every target edge at least `min_edge` long whose free space in front (min of 3 ray samples) is at least
`min_space`, up to `max_bars` bars parallel to the edge are placed: the first bar's near side is `distance` from the edge,
the next ones follow at centre pitch `pitch`; each bar is `inset` shorter than the edge at both ends. A bar is rejected (and
all further bars of that edge) when it comes closer than `clearance` to any target feature or to a bar of another edge.
Bars have the same tone as the main features (chrome in clear field, opening in dark field).

Printability: all bars are simulated together with the main mask; a bar prints when the resist feature occupies any sample on
its centre line. Printing bars are narrowed by 4 nm per pass; below `min_width` they are dropped. A final pass drops any bar
that still prints. verify_srafs can be re-run on the corrected main mask after OPC.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np
from shapely.geometry import Polygon as SPoly
from shapely.ops import unary_union

from ..geometry import Polygon
from .common import OPCOptions, ProcessModel, SRAFOptions, value_at
from .fragments import fragment_polygons
from .rule import free_space

SHRINK_STEP = 4.0
MAX_PASSES = 8


@dataclass
class Bar:
    p0: np.ndarray          # centre-line start
    p1: np.ndarray          # centre-line end
    width: float
    edge: int
    k: int

    def shapely(self) -> SPoly:
        u = self.p1 - self.p0
        u = u / np.hypot(*u)
        w = np.array([-u[1], u[0]]) * self.width / 2
        return SPoly([self.p0 + w, self.p1 + w, self.p1 - w, self.p0 - w])

    def polygon(self, layer: int, datatype: int) -> Polygon:
        s = self.shapely()
        return Polygon(np.asarray(s.exterior.coords)[:-1], [], layer, datatype)

    def samples(self, n: int = 9) -> np.ndarray:
        t = np.linspace(0.0, 1.0, n)[:, None]
        return self.p0[None] * (1 - t) + self.p1[None] * t


def place_srafs(target: Sequence[Polygon], opts: OPCOptions) -> List[Bar]:
    sr = opts.sraf
    fl = fragment_polygons(target, opts.fragments, whole_edges=True)
    U = unary_union([p.to_shapely() for p in fl.polys])
    bars: List[Bar] = []
    accepted: List[SPoly] = []
    accepted_edge: List[int] = []
    for ei, e in enumerate(fl.edges):
        if e.length < sr.min_edge:
            continue
        space = min(free_space(U, e.a + e.u * e.length * t, e.n, 2.0 * sr.min_space + 1000.0) for t in (0.25, 0.5, 0.75))
        if space < sr.min_space:
            continue
        a, b = e.a + e.u * sr.inset, e.b - e.u * sr.inset
        if np.hypot(*(b - a)) < 1e-6 or np.dot(b - a, e.u) <= 0:
            continue
        for k in range(sr.max_bars):
            off = sr.distance + sr.width / 2 + k * sr.pitch
            bar = Bar(a + e.n * off, b + e.n * off, sr.width, ei, k)
            sp = bar.shapely()
            if sp.distance(U) < sr.clearance - 1e-9:
                break
            others = [q for q, qe in zip(accepted, accepted_edge) if qe != ei]
            if others and sp.distance(unary_union(others)) < sr.clearance - 1e-9:
                break
            bars.append(bar)
            accepted.append(sp)
            accepted_edge.append(ei)
    return bars


def _printing(bars: Sequence[Bar], main: Sequence[Polygon], pm: ProcessModel, layer: int, dt: int) -> List[bool]:
    polys = list(main) + [b.polygon(layer, dt) for b in bars]
    grid = pm.grid_for(polys)
    I = pm.imager.image(polys, grid, tone=pm.tone, supersample=pm.supersample)
    F, lv = pm.resist.field(I, grid.pixel)
    out = []
    for b in bars:
        pts = b.samples()
        v = value_at(F, grid, pts[:, 0], pts[:, 1])
        out.append(bool(np.any(pm.inside_feature(v, lv))))
    return out


def verify_srafs(bars: Sequence[Bar], main: Sequence[Polygon], pm: ProcessModel, opts: OPCOptions, layer: int = 1,
                 dt: int = 0) -> List[Bar]:
    """Narrow / drop bars that print when simulated together with `main` (mask polygons)."""
    sr = opts.sraf
    bars = [Bar(b.p0, b.p1, b.width, b.edge, b.k) for b in bars]
    for _ in range(MAX_PASSES):
        if not bars:
            return []
        bad = _printing(bars, main, pm, layer, dt)
        if not any(bad):
            return bars
        for b, f in zip(bars, bad):
            if f:
                b.width -= SHRINK_STEP
        bars = [b for b in bars if b.width >= sr.min_width]
    if not bars:
        return []
    bad = _printing(bars, main, pm, layer, dt)
    return [b for b, f in zip(bars, bad) if not f]


def sraf_stage(target: Sequence[Polygon], pm: ProcessModel, opts: OPCOptions) -> List[Polygon]:
    """Place and (optionally) printability-check assists; returns polygons on the target's layer (or sr.layer_datatype)."""
    layer, dt = target[0].layer, target[0].datatype
    if opts.sraf.layer_datatype is not None:
        dt_out = opts.sraf.layer_datatype
    else:
        dt_out = dt
    bars = place_srafs(target, opts)
    if opts.sraf.check_print:
        bars = verify_srafs(bars, target, pm, opts, layer, dt_out)
    return [b.polygon(layer, dt_out) for b in bars]
