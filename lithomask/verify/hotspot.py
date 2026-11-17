"""Phase 7 - lithographic hotspot detection on the simulated print.

Inputs: target polygons (what should print), printed polygons (resist contours, nm), EPE samples/values (epe.py) and a CD map.
Hotspot kinds (all lengths nm):
  bridge    one printed component overlaps >= 2 separate target components (shorts); located at the printed area outside the target.
  break     one target component is covered by >= 2 printed components (open circuit); located at the missing target area.
  missing   a target component with (almost) no printed area.
  pinch     printed width / target width - 1 < -pinch_frac on a cutline (not on line-end edges); NaN width = feature absent = 'open'.
  lineend   line-end EPE < -lineend_tol (shortening / pull-back).
  extra     printed component that touches no target component (sidelobe / printing assist feature), area > extra_min_area.
`severity` is a positive number in nm (or nm^2 for extra/missing area) - larger is worse. Close hotspots of the same kind are merged
(`merge_radius`) keeping the worst.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np
from shapely.ops import unary_union

from ..geometry import Polygon
from .epe import CDMap, EdgeSamples


@dataclass
class HotspotRules:
    pinch_frac: float = 0.15          # printed width < (1 - pinch_frac) * target width
    lineend_tol: float = 25.0         # nm of line-end pull-back
    min_overlap_area: float = 400.0   # nm^2: smaller overlaps do not count for bridge / break / missing decisions
    extra_min_area: float = 2500.0    # nm^2: isolated printed blobs smaller than this are ignored
    merge_radius: float = 100.0       # nm


@dataclass
class Hotspot:
    kind: str
    x: float
    y: float
    severity: float
    detail: str = ""


def _components(geom):
    return [g for g in getattr(geom, "geoms", [geom]) if g.geom_type == "Polygon" and not g.is_empty]


def _merge(hs: List[Hotspot], radius: float) -> List[Hotspot]:
    out: List[Hotspot] = []
    for h in sorted(hs, key=lambda h: -h.severity):
        if not any(o.kind == h.kind and np.hypot(o.x - h.x, o.y - h.y) < radius for o in out):
            out.append(h)
    return out


def find_hotspots(target: Sequence[Polygon], printed: Sequence[Polygon], samples: EdgeSamples, epe: np.ndarray, cdm: CDMap,
                  rules: HotspotRules = None) -> List[Hotspot]:
    r = rules or HotspotRules()
    T = unary_union([p.to_shapely() for p in target]) if len(target) else None
    if T is None or T.is_empty:
        return []
    P = unary_union([p.to_shapely() for p in printed]) if len(printed) else None
    Tc = _components(T)
    Pc = _components(P) if P is not None and not P.is_empty else []
    hs: List[Hotspot] = []
    # --- topology: bridge / break / missing / extra
    for pc in Pc:
        touched = [t for t in Tc if pc.intersection(t).area > r.min_overlap_area]
        if len(touched) >= 2:
            extra = pc.difference(T)
            pieces = sorted(_components(extra), key=lambda g: -g.area)
            pt = (pieces[0] if pieces else pc).representative_point()
            hs.append(Hotspot("bridge", pt.x, pt.y, float(pc.area), f"printed component joins {len(touched)} target features"))
        elif not touched and pc.area >= r.extra_min_area:
            pt = pc.representative_point()
            hs.append(Hotspot("extra", pt.x, pt.y, float(pc.area), "printed blob not on any target feature"))
    for tc in Tc:
        cover = [pc for pc in Pc if pc.intersection(tc).area > r.min_overlap_area]
        if not cover:
            pt = tc.representative_point()
            hs.append(Hotspot("missing", pt.x, pt.y, float(tc.area), "target feature does not print"))
        elif len(cover) >= 2:
            miss = sorted(_components(tc.difference(unary_union(cover))), key=lambda g: -g.area)
            pt = (miss[0] if miss else tc).representative_point()
            hs.append(Hotspot("break", pt.x, pt.y, float(miss[0].area if miss else 0.0), f"feature printed as {len(cover)} pieces"))
    # --- line-end shortening
    if len(samples):
        for i in np.flatnonzero(samples.lineend & (epe < -r.lineend_tol)):
            hs.append(Hotspot("lineend", samples.points[i, 0], samples.points[i, 1], float(-epe[i]),
                              f"line-end pull-back {-epe[i]:.1f} nm"))
    # --- pinch / open on the CD map
    if len(cdm):
        ratio = cdm.printed_cd / cdm.target_cd - 1.0
        for i in range(len(cdm)):
            if np.isnan(ratio[i]):
                hs.append(Hotspot("pinch", cdm.x[i], cdm.y[i], float(cdm.target_cd[i]), "feature absent on cutline (open)"))
            elif ratio[i] < -r.pinch_frac:
                hs.append(Hotspot("pinch", cdm.x[i], cdm.y[i], float(-cdm.error[i]),
                                  f"printed {cdm.printed_cd[i]:.0f} vs target {cdm.target_cd[i]:.0f} nm"))
    return _merge(hs, r.merge_radius)


def summarize_hotspots(hs: Sequence[Hotspot]) -> Dict[str, int]:
    d: Dict[str, int] = {}
    for h in hs:
        d[h.kind] = d.get(h.kind, 0) + 1
    return d
