"""Phase 7 - mask rule check (MRC) with violation LOCATIONS (geometry only, no imaging).

Rules (all lengths nm; 0 / None = rule off):
  min_width      narrowest allowed feature width.  Violation = part of the mask that a mitre opening of radius min_width/2 removes.
  min_space      narrowest allowed gap.            Violation = gap region that a mitre closing of radius min_space/2 fills.
  min_area       smallest allowed connected feature (nm^2, after merging overlapping polygons).
  min_angle_deg  smallest allowed interior angle at a convex vertex (Manhattan corners are 90 deg; default 80 flags real acute tips).
  min_jog        shortest allowed edge between two corners that turn in OPPOSITE senses (a jog / notch step).
  grid           mask address grid: every vertex must lie on a multiple of it (reported once per polygon with the vertex count).
Width / space use the same mitre opening / closing as lithomask.opc.common.mrc_check but return where and how big each violation is;
slivers smaller than 2 % of w^2 (mitre artefacts at oblique edges) are ignored. `value` is a local estimate (twice the distance to the
nearest boundary / feature at the violation's representative point), `limit` the rule value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from ..geometry import Polygon


@dataclass
class MRCRules:
    min_width: float = 0.0
    min_space: float = 0.0
    min_area: float = 0.0
    min_angle_deg: float = 80.0
    min_jog: float = 0.0
    grid: float = 0.0


@dataclass
class Violation:
    rule: str            # 'width' | 'space' | 'area' | 'angle' | 'jog' | 'grid'
    x: float
    y: float
    value: float
    limit: float
    detail: str = ""


def _pieces(geom, min_area: float):
    geoms = getattr(geom, "geoms", [geom])
    out = [g for g in geoms if g.geom_type == "Polygon" and g.area > min_area]
    return sorted(out, key=lambda g: -g.area)


def _rings(p: Polygon):
    yield np.asarray(p.outer, float)
    for h in p.holes:
        yield np.asarray(h, float)


def run_mrc(polys: Sequence[Polygon], rules: Optional[MRCRules] = None) -> List[Violation]:
    r = rules or MRCRules()
    polys = [p for p in polys if len(p.outer) >= 3]
    out: List[Violation] = []
    if not polys:
        return out
    U = unary_union([p.to_shapely() for p in polys])
    if r.min_width > 0:
        w = r.min_width
        op = U.buffer(-w / 2, join_style=2).buffer(w / 2, join_style=2)
        for g in _pieces(U.difference(op), 0.02 * w * w):
            pt = g.representative_point()
            out.append(Violation("width", pt.x, pt.y, 2.0 * U.boundary.distance(pt), w, f"area {g.area:.0f} nm^2"))
    if r.min_space > 0:
        s = r.min_space
        cl = U.buffer(s / 2, join_style=2).buffer(-s / 2, join_style=2)
        for g in _pieces(cl.difference(U), 0.02 * s * s):
            pt = g.representative_point()
            out.append(Violation("space", pt.x, pt.y, 2.0 * U.distance(pt), s, f"area {g.area:.0f} nm^2"))
    if r.min_area > 0:
        for g in getattr(U, "geoms", [U]):
            if g.geom_type == "Polygon" and g.area < r.min_area:
                pt = g.representative_point()
                out.append(Violation("area", pt.x, pt.y, g.area, r.min_area, "feature area (nm^2)"))
    need_vertex = r.min_angle_deg > 0 or r.min_jog > 0 or r.grid > 0
    if need_vertex:
        for pi, p in enumerate(polys):
            off_grid, first_off = 0, None
            for P in _rings(p):
                M = len(P)
                e = np.roll(P, -1, axis=0) - P
                L = np.hypot(e[:, 0], e[:, 1])
                eh = e / np.maximum(L, 1e-12)[:, None]
                prev = np.roll(eh, 1, axis=0)
                cross = prev[:, 0] * eh[:, 1] - prev[:, 1] * eh[:, 0]
                dot = np.sum(prev * eh, axis=1)
                turn = np.arctan2(cross, dot)                       # signed turning angle at vertex i (start of edge i)
                convex = cross > 1e-9
                if r.min_angle_deg > 0:
                    for i in np.flatnonzero(convex):
                        interior = math.degrees(math.pi - turn[i])
                        if interior < r.min_angle_deg - 1e-6:
                            out.append(Violation("angle", P[i, 0], P[i, 1], interior, r.min_angle_deg, f"polygon {pi}"))
                if r.min_jog > 0:
                    for i in range(M):
                        j = (i + 1) % M
                        if 1e-9 < L[i] < r.min_jog and cross[i] * cross[j] < 0 and abs(cross[i]) > 1e-9 and abs(cross[j]) > 1e-9:
                            mid = 0.5 * (P[i] + P[j])
                            out.append(Violation("jog", mid[0], mid[1], float(L[i]), r.min_jog, f"polygon {pi}"))
                if r.grid > 0:
                    rem = np.abs(P / r.grid - np.round(P / r.grid)) * r.grid
                    bad = np.flatnonzero(np.any(rem > 1e-6, axis=1))
                    off_grid += len(bad)
                    if len(bad) and first_off is None:
                        first_off = P[bad[0]]
            if off_grid:
                out.append(Violation("grid", first_off[0], first_off[1], float(off_grid), r.grid,
                                     f"polygon {pi}: {off_grid} vertices off the {r.grid:g} nm grid"))
    return out


def summarize_mrc(violations: Sequence[Violation]) -> Dict[str, int]:
    d: Dict[str, int] = {}
    for v in violations:
        d[v.rule] = d.get(v.rule, 0) + 1
    return d
