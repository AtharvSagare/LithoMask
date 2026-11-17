"""Edge fragmentation and mask rebuild for edge-based OPC (rule-based and model-based stages).

Conventions (nm)
  * Every ring (outer boundary CCW, holes CW - see geometry.Polygon) has the MATERIAL of the feature on the LEFT of the travel
    direction. The outward normal of an edge with unit direction u = (ux, uy) is therefore n = (uy, -ux) for outer rings and
    holes alike (for a hole it points into the void).
  * A vertex is CONVEX when cross(u_prev, u_next) > 0 (left turn), CONCAVE when < 0.
  * A fragment is a piece [t0, t1] (nm from the edge start) of one edge. Its shift delta (nm) moves it along n; positive grows
    the feature. build() joins neighbouring fragments with jogs (same edge) or by intersecting the two shifted edge lines
    (corners), so Manhattan layouts stay Manhattan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

import numpy as np

from ..geometry import Polygon, union
from .common import FragmentOptions

MITER_LIMIT = 4.0       # corner miter longer than MITER_LIMIT * max(|shift|) is bevelled (two points) instead


@dataclass
class Edge:
    poly: int
    ring: int                 # global ring id
    a: np.ndarray             # start vertex
    b: np.ndarray             # end vertex
    u: np.ndarray             # unit direction
    n: np.ndarray             # outward unit normal
    length: float
    convex_a: bool            # convexity at the start vertex
    convex_b: bool            # convexity at the end vertex
    lineend: bool             # short edge between two convex corners


@dataclass
class Fragment:
    edge: int                 # index into FragmentedLayout.edges
    t0: float
    t1: float
    kind: str                 # 'corner' | 'run' | 'lineend' | 'whole'


@dataclass
class FragmentedLayout:
    polys: List[Polygon]
    edges: List[Edge] = field(default_factory=list)
    frags: List[Fragment] = field(default_factory=list)
    ring_edges: List[List[int]] = field(default_factory=list)       # per global ring: edge indices in order
    poly_rings: List[List[int]] = field(default_factory=list)       # per polygon: global ring ids (outer first)
    edge_frags: List[List[int]] = field(default_factory=list)       # per edge: fragment indices in order

    # ------------------------------------------------------------------ geometry of fragments
    def midpoints(self) -> np.ndarray:
        out = np.empty((len(self.frags), 2))
        for i, f in enumerate(self.frags):
            e = self.edges[f.edge]
            out[i] = e.a + e.u * 0.5 * (f.t0 + f.t1)
        return out

    def normals(self) -> np.ndarray:
        return np.array([self.edges[f.edge].n for f in self.frags]) if self.frags else np.zeros((0, 2))

    def lengths(self) -> np.ndarray:
        return np.array([f.t1 - f.t0 for f in self.frags])

    # ------------------------------------------------------------------ rebuild
    def build(self, deltas: Sequence[float]) -> List[Polygon]:
        """Mask polygons with fragment i shifted by deltas[i] (nm) along its outward normal."""
        d = np.asarray(deltas, float)
        out: List[Polygon] = []
        for pi, poly in enumerate(self.polys):
            rings = []
            for rid in self.poly_rings[pi]:
                rings.append(self._ring_points(rid, d))
            outer, holes = rings[0], rings[1:]
            cand = Polygon(outer, holes, poly.layer, poly.datatype)
            sp = cand.to_shapely()
            if sp.is_valid and not sp.is_empty:
                out.append(cand)
            else:
                fixed = sp.buffer(0)
                if not fixed.is_empty:
                    out.extend(Polygon.from_shapely(fixed, poly.layer, poly.datatype))
        return out

    def _ring_points(self, rid: int, d: np.ndarray) -> np.ndarray:
        eids = self.ring_edges[rid]
        m = len(eids)
        pts: List[np.ndarray] = []
        for k in range(m):
            e = self.edges[eids[k]]
            ep = self.edges[eids[k - 1]]
            fr_e = self.edge_frags[eids[k]]
            fr_p = self.edge_frags[eids[k - 1]]
            pts.extend(corner_points(e.a, ep, d[fr_p[-1]], e, d[fr_e[0]]))
            for i in range(len(fr_e) - 1):
                s = self.frags[fr_e[i]].t1
                base = e.a + e.u * s
                p1 = base + e.n * d[fr_e[i]]
                p2 = base + e.n * d[fr_e[i + 1]]
                pts.append(p1)
                if np.hypot(*(p2 - p1)) > 1e-9:
                    pts.append(p2)
        return np.array(pts)


def corner_points(V: np.ndarray, e1: Edge, d1: float, e2: Edge, d2: float) -> List[np.ndarray]:
    """Vertex V between edge e1 (shift d1) and edge e2 (shift d2) -> 1 point (line intersection) or 2 (bevel / collinear jog)."""
    p1 = V + e1.n * d1
    p2 = V + e2.n * d2
    cross = e1.u[0] * e2.u[1] - e1.u[1] * e2.u[0]
    if abs(cross) < 1e-9:
        return [p1] if np.hypot(*(p2 - p1)) < 1e-9 else [p1, p2]
    A = np.array([[e1.n[0], e1.n[1]], [e2.n[0], e2.n[1]]])
    w = np.linalg.solve(A, np.array([d1, d2]))
    if np.hypot(*w) > MITER_LIMIT * max(abs(d1), abs(d2), 1e-9) + 1e-6:
        return [p1, p2]
    return [V + w]


# ======================================================================================
# Fragmentation
# ======================================================================================
def split_edge(length: float, opt: FragmentOptions) -> List[Tuple[float, float, str]]:
    """Pieces (t0, t1, kind) of an edge of the given length. Short edges stay whole; longer ones get a corner fragment at each
    end and equal run fragments (<= max_len) in between."""
    if length < 2 * opt.corner_len + opt.min_len:
        return [(0.0, length, "run")]
    mid = length - 2 * opt.corner_len
    n = max(1, int(np.ceil(mid / opt.max_len - 1e-9)))
    step = mid / n
    cuts = [0.0, opt.corner_len] + [opt.corner_len + step * (i + 1) for i in range(n)] + [length]
    cuts[-2] = length - opt.corner_len
    kinds = ["corner"] + ["run"] * n + ["corner"]
    return [(cuts[i], cuts[i + 1], kinds[i]) for i in range(len(kinds))]


def fragment_polygons(polys: Sequence[Polygon], opt: FragmentOptions, whole_edges: bool = False) -> FragmentedLayout:
    """Union the target per (layer, datatype) and fragment every edge. whole_edges=True gives exactly one fragment per edge
    (used by the rule-based stage)."""
    merged: List[Polygon] = []
    groups = {}
    for p in polys:
        groups.setdefault(p.key, []).append(p)
    for (ly, dt), g in groups.items():
        for q in union(g):
            merged.append(Polygon(q.outer, q.holes, ly, dt))
    fl = FragmentedLayout(merged)
    for pi, poly in enumerate(merged):
        ids = []
        for ring in [poly.outer] + list(poly.holes):
            rid = len(fl.ring_edges)
            ids.append(rid)
            pts = _dedupe(ring)
            m = len(pts)
            vec = np.roll(pts, -1, axis=0) - pts
            ln = np.hypot(vec[:, 0], vec[:, 1])
            u = vec / ln[:, None]
            up = np.roll(u, 1, axis=0)                      # direction of the previous edge
            cr = up[:, 0] * u[:, 1] - up[:, 1] * u[:, 0]
            convex = cr > 0           # convexity at vertex k (between edge k-1 and edge k)
            eids = []
            for k in range(m):
                eid = len(fl.edges)
                eids.append(eid)
                conv_a, conv_b = bool(convex[k]), bool(convex[(k + 1) % m])
                fl.edges.append(Edge(pi, rid, pts[k].copy(), pts[(k + 1) % m].copy(), u[k].copy(),
                                     np.array([u[k][1], -u[k][0]]), float(ln[k]), conv_a, conv_b,
                                     conv_a and conv_b and ln[k] <= opt.lineend_max))
                pieces = [(0.0, float(ln[k]), "whole")] if whole_edges else split_edge(float(ln[k]), opt)
                fidx = []
                for (t0, t1, kind) in pieces:
                    if fl.edges[eid].lineend and kind != "whole":
                        kind = "lineend"
                    fidx.append(len(fl.frags))
                    fl.frags.append(Fragment(eid, t0, t1, kind))
                fl.edge_frags.append(fidx)
            fl.ring_edges.append(eids)
        fl.poly_rings.append(ids)
    return fl


def _dedupe(ring: np.ndarray) -> np.ndarray:
    """Drop repeated points (zero-length edges)."""
    keep = [0]
    for i in range(1, len(ring)):
        if np.hypot(*(ring[i] - ring[keep[-1]])) > 1e-9:
            keep.append(i)
    if len(keep) > 1 and np.hypot(*(ring[keep[-1]] - ring[keep[0]])) <= 1e-9:
        keep.pop()
    return ring[keep]
