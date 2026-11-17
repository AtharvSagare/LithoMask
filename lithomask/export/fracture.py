"""Phase 8 - fracturing of polygons into horizontal-sided trapezoids / rectangles.

Units: nm (float). Conventional fracture: cut horizontally at every distinct vertex y,
so each slab piece has horizontal top/bottom and straight (possibly slanted) left/right sides.
All x and y are snapped to the Pattern Unit (pu, nm), mimicking BEAMER's grid snapping.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple
import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.validation import make_valid

from ..geometry import Polygon


@dataclass(frozen=True)
class Trap:
    """Trapezoid with horizontal sides: bottom y0 (xl0..xr0), top y1 (xl1..xr1). nm."""
    y0: float
    y1: float
    xl0: float
    xr0: float
    xl1: float
    xr1: float
    layer: int = 1
    datatype: int = 0

    @property
    def area(self) -> float:
        return 0.5 * ((self.xr0 - self.xl0) + (self.xr1 - self.xl1)) * (self.y1 - self.y0)

    @property
    def is_rect(self) -> bool:
        return self.xl0 == self.xl1 and self.xr0 == self.xr1

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return (min(self.xl0, self.xl1), self.y0, max(self.xr0, self.xr1), self.y1)

    @property
    def centre(self) -> Tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return 0.5 * (x0 + x1), 0.5 * (y0 + y1)

    def to_polygon(self) -> Polygon:
        pts = [(self.xl0, self.y0), (self.xr0, self.y0), (self.xr1, self.y1), (self.xl1, self.y1)]
        out = []
        for p in pts:
            if not out or p != out[-1]:
                out.append(p)
        if len(out) > 1 and out[0] == out[-1]:
            out.pop()
        return Polygon(np.array(out, float), layer=self.layer, datatype=self.datatype)


@dataclass
class FractureResult:
    traps: List[Trap]
    source_area: Dict[Tuple[int, int], float]      # snapped, unioned area per (layer, datatype)
    traps_area: Dict[Tuple[int, int], float]
    pattern_unit: float

    @property
    def max_rel_area_error(self) -> float:
        worst = 0.0
        for k, a in self.source_area.items():
            if a > 0:
                worst = max(worst, abs(self.traps_area.get(k, 0.0) - a) / a)
        return worst

    @property
    def n_rect(self) -> int:
        return sum(t.is_rect for t in self.traps)


def _snap(v, pu):
    return float(np.floor(v / pu + 0.5) * pu) if pu > 0 else float(v)  # round half up


def _snap_arr(a, pu):
    return np.floor(np.asarray(a, float) / pu + 0.5) * pu


def snap_union(polys: Sequence[Polygon], pu: float):
    """Group by (layer, datatype), snap vertices to pu, union (no double exposure).
    Returns {(layer, datatype): shapely geometry}."""
    groups: Dict[Tuple[int, int], list] = {}
    for p in polys:
        q = Polygon(_snap_arr(p.outer, pu), [_snap_arr(h, pu) for h in p.holes], p.layer, p.datatype) if pu > 0 else p
        g = q.to_shapely()
        if not g.is_valid:
            g = make_valid(g)
        if g.is_empty or g.area <= 0:
            continue
        groups.setdefault((p.layer, p.datatype), []).append(g)
    return {k: unary_union(v) for k, v in groups.items()}


def iter_polys(g):
    if g.is_empty:
        return
    t = g.geom_type
    if t == "Polygon":
        yield g
    elif t in ("MultiPolygon", "GeometryCollection"):
        for s in g.geoms:
            yield from iter_polys(s)


def fracture_geometry(g, layer: int, datatype: int, pu: float = 1.0, merge: bool = True) -> List[Trap]:
    """Fracture a shapely (Multi)Polygon (already on the pu grid) into Trap objects."""
    traps: List[Trap] = []
    tol = max(pu, 1e-6) * 1e-3
    for poly in iter_polys(g):
        ys = set()
        for ring in [poly.exterior, *poly.interiors]:
            for _, y in ring.coords:
                ys.add(_snap(float(y), pu))
        ys = sorted(ys)
        minx, _, maxx, _ = poly.bounds
        for y0, y1 in zip(ys[:-1], ys[1:]):
            if y1 - y0 < tol:
                continue
            piece = poly.intersection(box(minx - 1.0, y0, maxx + 1.0, y1))
            for part in iter_polys(piece):
                if part.area < 1e-9:
                    continue
                c = np.asarray(part.exterior.coords)
                b = c[np.abs(c[:, 1] - y0) < tol, 0]
                t = c[np.abs(c[:, 1] - y1) < tol, 0]
                if len(b) == 0 or len(t) == 0:
                    continue  # numerically degenerate sliver
                xl0, xr0 = _snap(b.min(), pu), _snap(b.max(), pu)
                xl1, xr1 = _snap(t.min(), pu), _snap(t.max(), pu)
                if (xr0 - xl0) + (xr1 - xl1) <= 0:
                    continue
                traps.append(Trap(y0, y1, xl0, xr0, xl1, xr1, layer, datatype))
    return _merge_rects(traps) if merge else traps


def _merge_rects(traps: List[Trap]) -> List[Trap]:
    """Merge vertically adjacent rectangles with identical x extent (fewer shots)."""
    rects = sorted((t for t in traps if t.is_rect), key=lambda t: (t.xl0, t.xr0, t.y0))
    others = [t for t in traps if not t.is_rect]
    out: List[Trap] = []
    for t in rects:
        if out and out[-1].xl0 == t.xl0 and out[-1].xr0 == t.xr0 and abs(out[-1].y1 - t.y0) < 1e-9:
            p = out.pop()
            t = Trap(p.y0, t.y1, p.xl0, p.xr0, p.xl1, p.xr1, p.layer, p.datatype)
        out.append(t)
    return out + others


def fracture_polygons(polys: Iterable[Polygon], pattern_unit: float = 1.0, merge: bool = True) -> FractureResult:
    """Snap -> union per layer -> fracture. Reports area conservation per layer."""
    groups = snap_union(list(polys), pattern_unit)
    traps: List[Trap] = []
    src: Dict[Tuple[int, int], float] = {}
    for (ly, dt), g in groups.items():
        src[(ly, dt)] = float(g.area)
        traps.extend(fracture_geometry(g, ly, dt, pattern_unit, merge))
    ta: Dict[Tuple[int, int], float] = {}
    for t in traps:
        ta[(t.layer, t.datatype)] = ta.get((t.layer, t.datatype), 0.0) + t.area
    return FractureResult(traps, src, ta, pattern_unit)
