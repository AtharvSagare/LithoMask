"""Stage A - rule-based OPC: pitch-dependent edge bias, corner serifs / notches, line-end hammerheads, contact biasing.

Every rule acts on the TARGET geometry (no simulation). Bias is positive = feature grows (tone independent):
  * edge bias  : free space in front of each edge (ray cast along the outward normal, median of 3 samples) is interpolated
                 in RuleOptions.bias_table [(space_nm, bias_nm), ...]; the edge is moved by that bias.
  * contact    : small near-square features get an extra `contact_bias` on every edge.
  * line end   : an edge shorter than FragmentOptions.lineend_max between two convex corners gets `hammer_ext` extra outward
                 shift and two wings (`hammer_wing` wide, `hammer_len` deep) on either side.
  * serif      : square (side `serif`) centred on each convex corner; notch: square (side `notch`) removed at each concave
                 corner. Corners of hammerhead line ends are skipped when hammer_wing > 0.
Rules do not check each other: run mrc_check on the result (spacing next to wings and serifs can shrink).
"""
from __future__ import annotations

from typing import List, Sequence

import numpy as np
from shapely.geometry import LineString, Point
from shapely.geometry import Polygon as SPoly
from shapely.ops import unary_union

from ..geometry import Polygon
from .common import OPCOptions, RuleOptions
from .fragments import FragmentedLayout, corner_points, fragment_polygons


def free_space(U, point: np.ndarray, n: np.ndarray, max_space: float) -> float:
    """Distance (nm) from `point` along unit `n` to the next material of geometry U (max_space if none)."""
    eps = 0.5
    start = point + n * eps
    line = LineString([start, start + n * max_space])
    inter = line.intersection(U)
    if inter.is_empty:
        return max_space
    return float(min(max_space, eps + Point(start).distance(inter)))


def _bias_from_table(space: float, table) -> float:
    xs = [t[0] for t in table]
    ys = [t[1] for t in table]
    return float(np.interp(space, xs, ys))          # clamps outside the table range


def _square(cx: float, cy: float, side: float) -> SPoly:
    h = side / 2
    return SPoly([(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)])


def _is_contact(poly: Polygon, ro: RuleOptions) -> bool:
    if poly.holes:
        return False
    x0, y0, x1, y1 = poly.bbox
    w, h = x1 - x0, y1 - y0
    return max(w, h) <= ro.contact_max and max(w, h) <= 1.5 * min(w, h)


def rule_opc(target: Sequence[Polygon], opts: OPCOptions) -> List[Polygon]:
    ro = opts.rule
    fl = fragment_polygons(target, opts.fragments, whole_edges=True)
    U = unary_union([p.to_shapely() for p in fl.polys])
    bias = np.zeros(len(fl.frags))
    for i, f in enumerate(fl.frags):
        e = fl.edges[f.edge]
        samples = [free_space(U, e.a + e.u * e.length * t, e.n, ro.max_space) for t in (0.25, 0.5, 0.75)]
        b = _bias_from_table(float(np.median(samples)), ro.bias_table)
        if _is_contact(fl.polys[e.poly], ro):
            b += ro.contact_bias
        if e.lineend:
            b += ro.hammer_ext
        bias[i] = b
    mask = fl.build(bias)
    add, sub = [], []
    for rid, eids in enumerate(fl.ring_edges):
        m = len(eids)
        for k in range(m):
            e, ep = fl.edges[eids[k]], fl.edges[eids[k - 1]]
            de = bias[fl.edge_frags[eids[k]][0]]
            dp = bias[fl.edge_frags[eids[k - 1]][-1]]
            V = corner_points(e.a, ep, dp, e, de)[0]
            if e.convex_a:
                if ro.serif > 0 and not (ro.hammer_wing > 0 and (e.lineend or ep.lineend)):
                    add.append(_square(V[0], V[1], ro.serif))
            elif ro.notch > 0:
                sub.append(_square(V[0], V[1], ro.notch))
        if ro.hammer_wing > 0 and ro.hammer_len > 0:
            for eid in eids:
                e = fl.edges[eid]
                if not e.lineend:
                    continue
                t = bias[fl.edge_frags[eid][0]]                     # shift of the (already extended) end
                for s0, s1 in ((-ro.hammer_wing, 0.0), (e.length, e.length + ro.hammer_wing)):
                    pts = [e.a + e.u * s + e.n * tt for s, tt in ((s0, t - ro.hammer_len), (s1, t - ro.hammer_len),
                                                                    (s1, t), (s0, t))]
                    add.append(SPoly(pts))
    if not add and not sub:
        return mask
    G = unary_union([p.to_shapely() for p in mask] + add)
    if sub:
        G = G.difference(unary_union(sub))
    out: List[Polygon] = []
    groups = {p.key for p in mask}
    ly, dt = next(iter(groups)) if groups else (1, 0)
    out.extend(Polygon.from_shapely(G, ly, dt))
    return out
