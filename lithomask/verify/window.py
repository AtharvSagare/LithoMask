"""Phase 7 - process window: dose x defocus grid, exposure latitude (EL) and depth of focus (DOF).

Dose model: relative dose d scales the aerial image, I -> d * I, before the resist model (for a threshold resist this equals
threshold / d). Defocus f (nm) is applied through Imager.defocus_nm (kernels are cached per defocus). CD gauges are cutlines across
the target features chosen from the nominal CD map (smallest, median, largest target CD by default); a (dose, defocus) point PASSES
when EVERY gauge prints within +-cd_tol_pct of its target width (NaN = feature absent = fail).
EL(f) = 2 (d_hi - d_lo) / (d_hi + d_lo) * 100 %  over the contiguous passing dose run nearest d = 1 (edges linearly interpolated).
DOF = width of the contiguous defocus range around f = 0 (or the widest one if f = 0 fails) with EL >= el_min_pct (edges interpolated).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..geometry import Polygon
from ..resist import measure_cd
from ..opc.common import ProcessModel
from .epe import CDMap


@dataclass
class WindowOptions:
    doses: Sequence[float] = field(default_factory=lambda: tuple(np.round(np.linspace(0.85, 1.15, 13), 4)))
    defoci: Sequence[float] = field(default_factory=lambda: tuple(np.linspace(-300.0, 300.0, 7)))   # nm
    cd_tol_pct: float = 10.0
    el_min_pct: float = 5.0
    n_gauges: int = 3
    search: float = 150.0


@dataclass
class Gauge:
    x: float
    y: float
    nx: float
    ny: float
    target_cd: float


@dataclass
class WindowResult:
    doses: np.ndarray
    defoci: np.ndarray
    gauges: List[Gauge]
    cd: np.ndarray               # (n_gauge, n_defocus, n_dose) printed CD, NaN = absent
    dev_pct: np.ndarray          # (n_defocus, n_dose) worst |CD/target - 1| * 100 over gauges (inf where absent)
    ok: np.ndarray               # (n_defocus, n_dose) bool
    el_pct: np.ndarray           # (n_defocus,) exposure latitude, 0 where nothing passes
    dose_lo: np.ndarray          # (n_defocus,) interpolated passing dose range (NaN if none)
    dose_hi: np.ndarray
    dof_nm: float
    best_focus: float
    cd_tol_pct: float
    el_min_pct: float

    @property
    def max_el_pct(self) -> float:
        return float(self.el_pct.max()) if len(self.el_pct) else 0.0


def choose_gauges(cdm: CDMap, n: int = 3) -> List[Gauge]:
    ok = np.flatnonzero(np.isfinite(cdm.printed_cd))
    if len(ok) == 0:
        ok = np.arange(len(cdm))
    if len(ok) == 0:
        return []
    order = ok[np.argsort(cdm.target_cd[ok])]
    picks = sorted({int(order[int(round(k))]) for k in np.linspace(0, len(order) - 1, min(n, len(order)))})
    return [Gauge(cdm.x[i], cdm.y[i], cdm.nx[i], cdm.ny[i], float(cdm.target_cd[i])) for i in picks]


def _edges(dev: np.ndarray, doses: np.ndarray, tol: float) -> Tuple[float, float]:
    """Interpolated (lo, hi) dose edges of the passing run nearest dose 1; (nan, nan) when nothing passes."""
    ok = dev <= tol
    if not ok.any():
        return float("nan"), float("nan")
    runs, i = [], 0
    while i < len(ok):
        if ok[i]:
            j = i
            while j + 1 < len(ok) and ok[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    a, b = min(runs, key=lambda ab: (0 if doses[ab[0]] <= 1.0 <= doses[ab[1]] else 1, -(ab[1] - ab[0])))

    def cross(p, q):          # p passes, q fails; dose where dev == tol
        if not (np.isfinite(dev[q]) and dev[q] < 1e8):
            return doses[p]
        t = (tol - dev[p]) / (dev[q] - dev[p])
        return doses[p] + t * (doses[q] - doses[p])
    lo = cross(a, a - 1) if a > 0 else doses[a]
    hi = cross(b, b + 1) if b < len(doses) - 1 else doses[b]
    return float(lo), float(hi)


def _dof(defoci: np.ndarray, el: np.ndarray, el_min: float) -> Tuple[float, float]:
    ok = el >= el_min
    if not ok.any():
        return 0.0, float("nan")
    runs, i = [], 0
    while i < len(ok):
        if ok[i]:
            j = i
            while j + 1 < len(ok) and ok[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    a, b = min(runs, key=lambda ab: (0 if defoci[ab[0]] <= 0.0 <= defoci[ab[1]] else 1, -(ab[1] - ab[0])))

    def cross(p, q):
        t = (el_min - el[p]) / (el[q] - el[p])
        return defoci[p] + t * (defoci[q] - defoci[p])
    lo = cross(a, a - 1) if a > 0 else defoci[a]
    hi = cross(b, b + 1) if b < len(defoci) - 1 else defoci[b]
    return float(hi - lo), float(0.5 * (lo + hi))


def process_window(mask: Sequence[Polygon], pm: ProcessModel, gauges: Sequence[Gauge], grid, opts: Optional[WindowOptions] = None
                   ) -> WindowResult:
    o = opts or WindowOptions()
    doses = np.asarray(o.doses, float)
    defoci = np.asarray(o.defoci, float)
    feature = "dark" if pm.tone == "clear_field" else "bright"
    cd = np.full((len(gauges), len(defoci), len(doses)), np.nan)
    for fi, f in enumerate(defoci):
        im = replace(pm.imager, defocus_nm=float(f))
        I = im.image(mask, grid, tone=pm.tone, supersample=pm.supersample)
        for di, d in enumerate(doses):
            F, lv = pm.resist.field(d * I, grid.pixel)
            for gi, g in enumerate(gauges):
                n = np.array([g.nx, g.ny])
                mid = np.array([g.x, g.y])
                half = g.target_cd / 2 + o.search
                cd[gi, fi, di] = measure_cd(F, grid, mid - half * n, mid + half * n, lv, feature)
    tcd = np.array([g.target_cd for g in gauges])[:, None, None]
    with np.errstate(invalid="ignore"):
        dev = np.abs(cd / tcd - 1.0) * 100.0
    dev = np.where(np.isfinite(dev), dev, 1e9).max(axis=0) if len(gauges) else np.full((len(defoci), len(doses)), 1e9)
    ok = dev <= o.cd_tol_pct
    lo = np.full(len(defoci), np.nan)
    hi = np.full(len(defoci), np.nan)
    el = np.zeros(len(defoci))
    for fi in range(len(defoci)):
        lo[fi], hi[fi] = _edges(dev[fi], doses, o.cd_tol_pct)
        if np.isfinite(lo[fi]):
            el[fi] = 200.0 * (hi[fi] - lo[fi]) / (hi[fi] + lo[fi])
    dof, bf = _dof(defoci, el, o.el_min_pct)
    return WindowResult(doses, defoci, list(gauges), cd, dev, ok, el, lo, hi, dof, bf, o.cd_tol_pct, o.el_min_pct)
