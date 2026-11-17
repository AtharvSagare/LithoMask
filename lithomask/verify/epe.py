"""Phase 7 - EPE and CD measurement along the target outline.

Conventions (same as Phase 5): EPE (nm) = signed distance along the OUTWARD normal of the target edge from the target edge to the
printed contour; > 0 printed too large, < 0 too small / pulled back. Polygon outer rings are CCW and holes CW (lithomask.geometry), so
for both the outward normal of the material at edge direction (dx, dy) is (dy, -dx).

edge_samples : sample points every ~`step` nm along every target edge, skipping `corner_exclude` nm next to each vertex (corner
               rounding is physical and would dominate every statistic); line-end edges (short edge between two convex vertices)
               get a single sample at their middle.
cd_map       : at every sample, the target width across the feature (ray from the edge into the material, up to `max_cd`) and the
               printed width of the same feature measured on the same cutline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
from shapely.geometry import LineString, Point

from ..geometry import Polygon
from ..optics import Grid
from ..resist import measure_cd


@dataclass
class EdgeSamples:
    points: np.ndarray        # (N, 2) nm
    normals: np.ndarray       # (N, 2) unit outward normals
    edge_id: np.ndarray       # (N,) int, one id per target edge
    edge_len: np.ndarray      # (N,) length of the edge the sample is on (nm)
    lineend: np.ndarray       # (N,) bool: sample lies on a line-end edge

    def __len__(self) -> int:
        return len(self.points)


def edge_samples(polys: Sequence[Polygon], step: float = 20.0, lineend_max: float = 220.0,
                 corner_exclude: float = 20.0) -> EdgeSamples:
    pts, nrm, eid, elen, le = [], [], [], [], []
    counter = 0
    for p in polys:
        for ring in [p.outer] + list(p.holes):
            P = np.asarray(ring, float)
            M = len(P)
            if M < 3:
                continue
            e = np.roll(P, -1, axis=0) - P
            L = np.hypot(e[:, 0], e[:, 1])
            eh = e / np.maximum(L, 1e-12)[:, None]
            prev = np.roll(eh, 1, axis=0)
            convex = (prev[:, 0] * eh[:, 1] - prev[:, 1] * eh[:, 0]) > 1e-9     # material-convex vertex i (start of edge i)
            for i in range(M):
                if L[i] < 1e-9:
                    continue
                n = np.array([eh[i, 1], -eh[i, 0]])
                is_le = bool(L[i] <= lineend_max and convex[i] and convex[(i + 1) % M])
                if is_le:
                    s = np.array([0.5 * L[i]])
                else:
                    k = max(1, int(round(L[i] / step)))
                    s = (np.arange(k) + 0.5) * L[i] / k
                    keep = (s >= corner_exclude) & (L[i] - s >= corner_exclude)
                    s = s[keep] if keep.any() else np.array([0.5 * L[i]])
                for si in s:
                    pts.append(P[i] + eh[i] * si)
                    nrm.append(n)
                    eid.append(counter)
                    elen.append(L[i])
                    le.append(is_le)
                counter += 1
    if not pts:
        z = np.zeros((0, 2))
        return EdgeSamples(z, z.copy(), np.zeros(0, int), np.zeros(0), np.zeros(0, bool))
    return EdgeSamples(np.array(pts), np.array(nrm), np.array(eid, int), np.array(elen), np.array(le, bool))


@dataclass
class CDMap:
    x: np.ndarray             # mid point of the cutline (centre of the target feature across the cut)
    y: np.ndarray
    nx: np.ndarray            # cutline direction (unit, = outward normal of the sampled edge)
    ny: np.ndarray
    target_cd: np.ndarray
    printed_cd: np.ndarray    # NaN where the feature does not print at the cut

    def __len__(self) -> int:
        return len(self.x)

    @property
    def error(self) -> np.ndarray:
        return self.printed_cd - self.target_cd


def cd_map(samples: EdgeSamples, target_union, F: np.ndarray, level: float, grid: Grid, feature: str,
           max_cd: float = 800.0, search: float = 150.0, max_samples: int = 4000) -> CDMap:
    idx = np.arange(len(samples))
    if len(idx) > max_samples:
        idx = idx[np.linspace(0, len(idx) - 1, max_samples).astype(int)]
    xs, ys, nxs, nys, tcd, pcd = [], [], [], [], [], []
    for i in idx:
        p, n = samples.points[i], samples.normals[i]
        p_in = p - 0.01 * n
        inter = target_union.intersection(LineString([p_in, p_in - n * max_cd]))
        geoms = [g for g in getattr(inter, "geoms", [inter]) if g.geom_type == "LineString"]
        if not geoms:
            continue
        g0 = min(geoms, key=lambda g: g.distance(Point(p_in)))
        if g0.distance(Point(p_in)) > 1e-3:
            continue
        Lt = g0.length + 0.01
        if Lt < 1.0 or Lt >= max_cd - 1.0:
            continue
        mid = p - n * Lt / 2
        half = Lt / 2 + search
        w = measure_cd(F, grid, mid - half * n, mid + half * n, level, feature)
        xs.append(mid[0]); ys.append(mid[1]); nxs.append(n[0]); nys.append(n[1]); tcd.append(Lt); pcd.append(w)
    f = lambda a: np.array(a, float)
    return CDMap(f(xs), f(ys), f(nxs), f(nys), f(tcd), f(pcd))
