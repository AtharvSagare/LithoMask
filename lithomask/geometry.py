"""lithomask.geometry - polygon model, primitives and operations.

Conventions
-----------
* Coordinates are float NANOMETRES in a right-handed x(right)/y(up) frame.
* Polygon.outer is counter-clockwise, Polygon.holes are clockwise (normalised on construction).
* Rings are stored WITHOUT a repeated closing vertex.
* Angles are in degrees, counter-clockwise positive.
* Booleans/offsets use shapely (GEOS) in float precision; results are re-normalised to the convention above.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point
from shapely.geometry import Polygon as _SPoly
from shapely.geometry import MultiPolygon as _SMulti

Layer = Tuple[int, int]  # (gds layer number, datatype)


def _as_ring(points: Iterable[Sequence[float]]) -> np.ndarray:
    a = np.asarray(list(points), dtype=float).reshape(-1, 2)
    if len(a) > 1 and np.allclose(a[0], a[-1]):
        a = a[:-1]
    return a


def _signed_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


@dataclass
class Polygon:
    """Simple polygon with optional holes, on one (layer, datatype)."""

    outer: np.ndarray
    holes: List[np.ndarray] = field(default_factory=list)
    layer: int = 1
    datatype: int = 0

    def __post_init__(self) -> None:
        self.outer = _as_ring(self.outer)
        self.holes = [_as_ring(h) for h in self.holes]
        if len(self.outer) >= 3 and _signed_area(self.outer) < 0:
            self.outer = self.outer[::-1].copy()
        self.holes = [h[::-1].copy() if (len(h) >= 3 and _signed_area(h) > 0) else h for h in self.holes]

    # ---- basic properties -------------------------------------------------
    @property
    def key(self) -> Layer:
        return (self.layer, self.datatype)

    @property
    def area(self) -> float:
        """Net area in nm^2 (outer minus holes)."""
        return abs(_signed_area(self.outer)) - sum(abs(_signed_area(h)) for h in self.holes)

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        mn, mx = self.outer.min(axis=0), self.outer.max(axis=0)
        return float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1])

    @property
    def num_vertices(self) -> int:
        return len(self.outer) + sum(len(h) for h in self.holes)

    def copy(self) -> "Polygon":
        return Polygon(self.outer.copy(), [h.copy() for h in self.holes], self.layer, self.datatype)

    # ---- transforms (return new objects) ---------------------------------------
    def _map(self, f) -> "Polygon":
        return Polygon(f(self.outer), [f(h) for h in self.holes], self.layer, self.datatype)

    def translate(self, dx: float, dy: float) -> "Polygon":
        d = np.array([dx, dy], float)
        return self._map(lambda r: r + d)

    def rotate(self, angle_deg: float, center: Sequence[float] = (0.0, 0.0)) -> "Polygon":
        t = math.radians(angle_deg)
        c, s = math.cos(t), math.sin(t)
        R = np.array([[c, -s], [s, c]])
        o = np.asarray(center, float)
        return self._map(lambda r: (r - o) @ R.T + o)

    def mirror(self, axis: str = "x", center: float = 0.0) -> "Polygon":
        """Mirror across a line. axis='x': reflect y about y=center (x-axis mirror); axis='y': reflect x about x=center."""
        if axis == "x":
            return self._map(lambda r: np.column_stack([r[:, 0], 2 * center - r[:, 1]]))
        if axis == "y":
            return self._map(lambda r: np.column_stack([2 * center - r[:, 0], r[:, 1]]))
        raise ValueError("axis must be 'x' or 'y'")

    def scale(self, sx: float, sy: Optional[float] = None, center: Sequence[float] = (0.0, 0.0)) -> "Polygon":
        sy = sx if sy is None else sy
        o = np.asarray(center, float)
        k = np.array([sx, sy], float)
        return self._map(lambda r: (r - o) * k + o)

    def snap(self, grid: float) -> "Polygon":
        """Round vertices to a grid (nm) and drop consecutive duplicates."""
        def f(r):
            q = np.round(r / grid) * grid
            keep = np.ones(len(q), bool)
            keep[1:] = np.any(q[1:] != q[:-1], axis=1)
            q = q[keep]
            if len(q) > 1 and np.all(q[0] == q[-1]):
                q = q[:-1]
            return q
        return self._map(f)

    # ---- shapely bridge -------------------------------------------------------
    def to_shapely(self) -> _SPoly:
        return _SPoly(self.outer, holes=self.holes)

    @staticmethod
    def from_shapely(g, layer: int = 1, datatype: int = 0) -> List["Polygon"]:
        """Convert a shapely (Multi)Polygon / collection to a list of Polygon; ignores non-areal parts."""
        out: List[Polygon] = []
        if g is None or g.is_empty:
            return out
        if isinstance(g, _SPoly):
            out.append(Polygon(np.asarray(g.exterior.coords)[:-1],
                               [np.asarray(i.coords)[:-1] for i in g.interiors], layer, datatype))
        elif hasattr(g, "geoms"):
            for sub in g.geoms:
                out.extend(Polygon.from_shapely(sub, layer, datatype))
        return out

    # ---- checks ---------------------------------------------------------------
    def check(self, min_area: float = 0.0) -> List[str]:
        """Return a list of human-readable problems (empty list = valid)."""
        issues: List[str] = []
        if len(self.outer) < 3:
            issues.append("outer ring has fewer than 3 vertices")
            return issues
        if np.any(np.all(self.outer == np.roll(self.outer, -1, axis=0), axis=1)):
            issues.append("duplicate consecutive vertices")
        if self.area <= max(min_area, 0.0):
            issues.append("zero or negative net area")
        sp = self.to_shapely()
        if not sp.is_valid:
            issues.append("invalid geometry: " + shapely.is_valid_reason(sp))
        return issues

    def is_valid(self) -> bool:
        return not self.check()

    def __repr__(self) -> str:
        return f"Polygon(n={len(self.outer)}, holes={len(self.holes)}, area={self.area:.4g} nm^2, layer={self.layer}/{self.datatype})"


# ======================================================================================
# Primitives
# ======================================================================================
def rectangle(x0: float, y0: float, x1: float, y1: float, layer: int = 1, datatype: int = 0) -> Polygon:
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    return Polygon([(xa, ya), (xb, ya), (xb, yb), (xa, yb)], [], layer, datatype)


def polygon(points: Iterable[Sequence[float]], layer: int = 1, datatype: int = 0) -> Polygon:
    return Polygon(_as_ring(points), [], layer, datatype)


def _n_for_sagitta(r: float, tol: float, n_min: int = 8) -> int:
    """Smallest N so that the max deviation (sagitta) of an inscribed N-gon from radius r is <= tol."""
    if tol <= 0 or tol >= r:
        return n_min
    return max(n_min, int(math.ceil(math.pi / math.acos(1.0 - tol / r))))


def ellipse(cx: float, cy: float, rx: float, ry: float, tol: float = 1.0, rotation: float = 0.0,
            layer: int = 1, datatype: int = 0, n: Optional[int] = None) -> Polygon:
    """Ellipse as an inscribed N-gon (vertices ON the ellipse). tol = max sagitta in nm, sized on the larger radius."""
    n = n or _n_for_sagitta(max(rx, ry), tol)
    t = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    pts = np.column_stack([cx + rx * np.cos(t), cy + ry * np.sin(t)])
    p = Polygon(pts, [], layer, datatype)
    return p.rotate(rotation, (cx, cy)) if rotation else p


def circle(cx: float, cy: float, r: float, tol: float = 1.0, layer: int = 1, datatype: int = 0,
           n: Optional[int] = None) -> Polygon:
    return ellipse(cx, cy, r, r, tol=tol, layer=layer, datatype=datatype, n=n)


def regular_polygon(cx: float, cy: float, r: float, sides: int, rotation: float = 0.0,
                    layer: int = 1, datatype: int = 0) -> Polygon:
    """Regular polygon with circumradius r; first vertex at angle `rotation` degrees."""
    if sides < 3:
        raise ValueError("sides must be >= 3")
    t = math.radians(rotation) + np.linspace(0.0, 2 * math.pi, sides, endpoint=False)
    return Polygon(np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)]), [], layer, datatype)


def ring(cx: float, cy: float, r_in: float, r_out: float, tol: float = 1.0, layer: int = 1, datatype: int = 0) -> Polygon:
    if not 0 < r_in < r_out:
        raise ValueError("require 0 < r_in < r_out")
    o = circle(cx, cy, r_out, tol, layer, datatype)
    i = circle(cx, cy, r_in, tol, layer, datatype)
    return Polygon(o.outer, [i.outer], layer, datatype)


def path(points: Iterable[Sequence[float]], width: float, end: str = "flat", join: str = "miter",
         tol: float = 1.0, layer: int = 1, datatype: int = 0) -> List[Polygon]:
    """Wire/path of given width along a polyline. end: 'flat'|'square'|'round'; join: 'miter'|'round'|'bevel'.
    Returns a list (a self-overlapping path may give several polygons after union)."""
    cap = {"flat": "flat", "square": "square", "round": "round"}[end]
    jn = {"miter": "mitre", "round": "round", "bevel": "bevel"}[join]
    res = max(4, _n_for_sagitta(width / 2.0, tol) // 4)
    g = LineString(_as_ring_open(points)).buffer(width / 2.0, cap_style=cap, join_style=jn, quad_segs=res, mitre_limit=10.0)
    return Polygon.from_shapely(g, layer, datatype)


def _as_ring_open(points) -> np.ndarray:
    return np.asarray(list(points), dtype=float).reshape(-1, 2)


# ======================================================================================
# Operations
# ======================================================================================
def _union_shapely(polys: Sequence[Polygon]):
    return shapely.union_all([p.to_shapely() for p in polys]) if polys else _SPoly()


def boolean(a: Sequence[Polygon], b: Sequence[Polygon], op: str, layer: Optional[int] = None,
            datatype: Optional[int] = None) -> List[Polygon]:
    """Boolean of two polygon sets. op in {'or','and','not','xor'} ('not' = a minus b).
    Result layer defaults to that of the first polygon in `a` (or `b`)."""
    ga, gb = _union_shapely(list(a)), _union_shapely(list(b))
    g = {"or": ga.union, "and": ga.intersection, "not": ga.difference, "xor": ga.symmetric_difference}[op](gb)
    ref = (list(a) + list(b))[0] if (len(a) + len(b)) else None
    L = layer if layer is not None else (ref.layer if ref else 1)
    D = datatype if datatype is not None else (ref.datatype if ref else 0)
    return Polygon.from_shapely(g, L, D)


def union(polys: Sequence[Polygon]) -> List[Polygon]:
    return boolean(polys, [], "or")


def offset(polys: Sequence[Polygon], delta: float, join: str = "miter", miter_limit: float = 2.0,
           tol: float = 1.0) -> List[Polygon]:
    """Grow (delta>0) or shrink (delta<0) polygons by |delta| nm. join: 'miter' | 'round' | 'bevel'.
    'miter' keeps sharp convex corners (limit = miter_limit x |delta|), the usual choice for mask bias."""
    if not polys:
        return []
    jn = {"miter": "mitre", "round": "round", "bevel": "bevel"}[join]
    res = max(4, _n_for_sagitta(abs(delta), tol) // 4) if delta else 4
    g = _union_shapely(list(polys)).buffer(delta, join_style=jn, mitre_limit=miter_limit, quad_segs=res)
    return Polygon.from_shapely(g, polys[0].layer, polys[0].datatype)


def bbox_of(polys: Sequence[Polygon]) -> Tuple[float, float, float, float]:
    bb = np.array([p.bbox for p in polys])
    return float(bb[:, 0].min()), float(bb[:, 1].min()), float(bb[:, 2].max()), float(bb[:, 3].max())


def total_area(polys: Sequence[Polygon]) -> float:
    """Area of the UNION of the polygons (overlaps counted once), nm^2."""
    return float(_union_shapely(list(polys)).area)
