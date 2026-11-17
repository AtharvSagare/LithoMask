"""Phase 6 - calibration forward model, parameter handling and cost functions.

Cost functions ([Feng24] Eq.7-8, see notes/formulas.md)
  e_i   = simulated - measured   (nm), one per gauge; wt_i = gauge weight
  Eq.7  RMS     = sqrt( sum_i wt_i e_i^2 / sum_i wt_i )                                  (all gauges)
  Eq.8  RMS_new = sqrt( sum_{i in CD,EP} wt_i e_i^2 / sum_{i in CD,EP} wt_i
                        + wt_space * sum_{j in SPACE} wt_j e_j^2 / sum_{j in SPACE} wt_j )
  Eq.8 balances the few space gauges against the many CD gauges through `wt_space`. TODO-VERIFY: [Feng24] defines e_CD for the
  first sum; here EP gauges are put in that group too (they are edge-type, CD-like gauges). With no SPACE gauge Eq.8 == Eq.7
  restricted to the CD/EP group.
  Both are returned as a *residual vector* r with sum(r^2) = cost^2, r_i = s_i e_i, so the same object feeds
  differential_evolution (scalar cost) and least_squares (vector).

Failed measurements (feature missing / bridged / no contour inside the search range) get e_i = +`nan_penalty` nm, are flagged
`failed` in the evaluation and are reported; they keep the cost finite for the optimisers.

Parameters
  * resist parameters: everything exposed by ResistModel.param_names / get_vector / bounds (Phase 4).
  * optical parameters: 'na' and 'defocus_nm' of the Imager (each new value rebuilds the SOCS kernels -> slower; keep the
    optics fixed unless the data demand it). Dose is NOT a parameter: for ConstantThreshold / VariableThreshold / CompactResist
    a dose scale is absorbed by threshold / t0 / c_0, so fitting both would be degenerate.
  * any parameter can be fixed (excluded from the optimisation) and given custom bounds.
The aerial images are cached per pattern for the current optics, so resist-only calibration images each pattern exactly once.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from ..geometry import Polygon
from ..opc.common import ProcessModel
from ..optics import Grid, Imager
from ..resist import ResistModel, edge_placement, measure_cd
from .gauges import Gauge, GaugeSet

OPTICAL_PARAMS = ("na", "defocus_nm")


# ======================================================================================
# Cost functions
# ======================================================================================
def cost_scales(kinds: Sequence[str], weights: Sequence[float], mode: str = "eq7", wt_space: float = 1.0) -> np.ndarray:
    """Per-gauge factors s_i so that r_i = s_i * e_i satisfies sum(r^2) = RMS^2 (mode 'eq7') or RMS_new^2 (mode 'eq8')."""
    kinds = np.asarray(list(kinds))
    w = np.asarray(weights, float)
    s = np.zeros(len(w))
    if mode == "eq7":
        W = w.sum()
        if W > 0:
            s = np.sqrt(w / W)
    elif mode == "eq8":
        if wt_space < 0:
            raise ValueError("wt_space must be >= 0")
        for sel, c in ((kinds != "SPACE", 1.0), (kinds == "SPACE", float(wt_space))):
            W = w[sel].sum()
            if W > 0:
                s[sel] = np.sqrt(c * w[sel] / W)
    else:
        raise ValueError("cost mode must be 'eq7' or 'eq8'")
    return s


def combine_cost(errors: Sequence[float], kinds: Sequence[str], weights: Sequence[float], mode: str = "eq7",
                 wt_space: float = 1.0) -> float:
    """RMS (Eq.7) or RMS_new (Eq.8) from gauge errors (nm)."""
    r = cost_scales(kinds, weights, mode, wt_space) * np.asarray(errors, float)
    return float(np.sqrt(np.sum(r * r)))


# ======================================================================================
# Parameters
# ======================================================================================
@dataclass
class Param:
    name: str
    value: float
    lower: float
    upper: float
    fixed: bool = False
    group: str = "resist"          # 'resist' | 'optics'

    def __post_init__(self):
        if not self.upper > self.lower:
            raise ValueError(f"parameter {self.name}: upper bound must exceed lower bound ({self.lower}, {self.upper})")
        self.value = float(np.clip(self.value, self.lower, self.upper))


@dataclass
class ParameterSet:
    params: List[Param] = field(default_factory=list)

    @staticmethod
    def from_process(process: ProcessModel, optics: Sequence[str] = (), bounds: Optional[Mapping[str, Tuple[float, float]]] = None,
                     fixed: Iterable[str] = (), start: Optional[Mapping[str, float]] = None) -> "ParameterSet":
        """Parameters of `process`: all resist parameters plus the listed optical ones ('na', 'defocus_nm').
        bounds: name -> (lo, hi) overrides; fixed: names excluded from the optimisation; start: name -> starting value
        (default: the value currently in `process`). Starting values are clipped into the bounds."""
        bounds = dict(bounds or {})
        start = dict(start or {})
        fixed = set(fixed)
        out: List[Param] = []
        res = process.resist
        for n, v, b in zip(res.param_names(), res.get_vector(), res.bounds()):
            out.append(Param(n, float(v), b[0], b[1], group="resist"))
        im = process.imager
        for n in optics:
            if n not in OPTICAL_PARAMS:
                raise ValueError(f"unknown optical parameter {n!r}; allowed: {OPTICAL_PARAMS}")
            v = float(getattr(im, n))
            if n == "na":
                b = (0.5 * v, min(1.5 * v, 0.98 * im.n_image))
            else:
                b = (-200.0, 200.0)
            out.append(Param(n, v, b[0], b[1], group="optics"))
        names = [p.name for p in out]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate parameter names: {names}")
        for label, d in (("bounds", bounds), ("start", start)):
            bad = [k for k in d if k not in names]
            if bad:
                raise ValueError(f"{label}: unknown parameter(s) {bad}; available: {names}")
        bad = [k for k in fixed if k not in names]
        if bad:
            raise ValueError(f"fixed: unknown parameter(s) {bad}; available: {names}")
        for p in out:
            if p.name in bounds:
                p.lower, p.upper = float(bounds[p.name][0]), float(bounds[p.name][1])
                if not p.upper > p.lower:
                    raise ValueError(f"bounds for {p.name}: upper must exceed lower")
            if p.name in start:
                p.value = float(start[p.name])
            p.value = float(np.clip(p.value, p.lower, p.upper))
            p.fixed = p.name in fixed
        return ParameterSet(out)

    # ---------------------------------------------------------------- views
    @property
    def names(self) -> List[str]:
        return [p.name for p in self.params]

    @property
    def free(self) -> List[Param]:
        return [p for p in self.params if not p.fixed]

    @property
    def free_names(self) -> List[str]:
        return [p.name for p in self.free]

    def values(self) -> np.ndarray:
        return np.array([p.value for p in self.params], float)

    def free_values(self) -> np.ndarray:
        return np.array([p.value for p in self.free], float)

    def free_bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        f = self.free
        return np.array([p.lower for p in f], float), np.array([p.upper for p in f], float)

    def full_from_free(self, x_free: Sequence[float]) -> np.ndarray:
        """Full parameter vector with the free entries replaced by x_free (fixed ones keep their value)."""
        v = self.values()
        idx = [i for i, p in enumerate(self.params) if not p.fixed]
        if len(idx) != len(x_free):
            raise ValueError(f"expected {len(idx)} free values, got {len(x_free)}")
        v[idx] = np.asarray(x_free, float)
        return v

    def as_dict(self, full: Optional[Sequence[float]] = None) -> Dict[str, float]:
        v = self.values() if full is None else np.asarray(full, float)
        return {p.name: float(x) for p, x in zip(self.params, v)}


# ======================================================================================
# Evaluation container
# ======================================================================================
@dataclass
class Evaluation:
    values: Dict[str, float]            # all parameters
    simulated: np.ndarray               # per gauge, nm (NaN where the measurement failed)
    errors: np.ndarray                  # simulated - measured, nm (failed -> +nan_penalty)
    failed: np.ndarray                  # bool per gauge
    residuals: np.ndarray               # s_i * e_i
    cost: float                         # RMS (Eq.7) or RMS_new (Eq.8)
    rms_by_kind: Dict[str, float]       # unweighted-by-cost-mode weighted RMS per kind (Eq.7 restricted to the kind), NaN if none
    mode: str = "eq7"


# ======================================================================================
# Problem
# ======================================================================================
class CalibrationProblem:
    """Gauges + mask patterns + a process model + a parameter set -> residual vector / cost.

    process   ProcessModel whose resist (and optionally imager) are calibrated. It is copied, never modified.
    patterns  {pattern id: list of mask Polygon} in the frame of the gauge coordinates.
    gauges    GaugeSet (or list of Gauge).
    params    ParameterSet (default: all resist parameters, optics fixed).
    cost      'eq7' or 'eq8';  wt_space only used by 'eq8'.
    """

    def __init__(self, process: ProcessModel, patterns: Mapping[str, Sequence[Polygon]], gauges,
                 params: Optional[ParameterSet] = None, cost: str = "eq7", wt_space: float = 1.0, nan_penalty: float = 100.0):
        self.gauges: GaugeSet = gauges if isinstance(gauges, GaugeSet) else GaugeSet(list(gauges))
        if len(self.gauges) == 0:
            raise ValueError("no gauges")
        missing = [p for p in self.gauges.pattern_ids() if p not in patterns]
        if missing:
            raise ValueError(f"gauges refer to unknown pattern id(s) {missing}; available: {sorted(patterns)}")
        empty = [p for p in self.gauges.pattern_ids() if len(patterns[p]) == 0]
        if empty:
            raise ValueError(f"pattern(s) {empty} contain no polygons")
        if cost not in ("eq7", "eq8"):
            raise ValueError("cost must be 'eq7' or 'eq8'")
        self.mode, self.wt_space, self.nan_penalty = cost, float(wt_space), float(nan_penalty)
        self.patterns = {k: list(patterns[k]) for k in self.gauges.pattern_ids()}
        self.tone, self.pixel_nm = process.tone, process.pixel
        self.supersample = process.supersample
        self._process_template = process
        self._base_imager: Imager = process.imager
        self.resist: ResistModel = copy.deepcopy(process.resist)
        self.params = params if params is not None else ParameterSet.from_process(process)
        self._check_params_match()
        self.grids: Dict[str, Grid] = {k: process.grid_for(v) for k, v in self.patterns.items()}
        self._feature = "dark" if self.tone == "clear_field" else "bright"
        self._refresh_scales()
        self._optics_names = [p.name for p in self.params.params if p.group == "optics"]
        self._optics_key: Optional[tuple] = None
        self._imager: Imager = self._base_imager
        self._aerial: Dict[str, np.ndarray] = {}
        self.n_evals = 0

    # ---------------------------------------------------------------- variants
    def derive(self, gauges=None, resist: Optional[ResistModel] = None, params: Optional[ParameterSet] = None,
               cost: Optional[str] = None, wt_space: Optional[float] = None) -> "CalibrationProblem":
        """A variant problem (new gauges and / or resist model and / or parameter set and / or cost) that SHARES the cached aerial
        images when neither this problem nor the variant fits optical parameters -- the expensive imaging is then done once.
        New gauges must refer to patterns this problem already has. `resist` replaces the resist model (its parameter set is rebuilt
        with default bounds unless `params` is given)."""
        new = copy.copy(self)
        if gauges is not None:
            gs = gauges if isinstance(gauges, GaugeSet) else GaugeSet(list(gauges))
            if len(gs) == 0:
                raise ValueError("no gauges")
            missing = [p for p in gs.pattern_ids() if p not in self.grids]
            if missing:
                raise ValueError(f"gauges refer to patterns this problem does not have: {missing}")
            new.gauges = gs
            new.patterns = {k: self.patterns[k] for k in gs.pattern_ids()}
        new.resist = copy.deepcopy(resist if resist is not None else self.resist)
        if params is None:
            if resist is not None:
                params = ParameterSet.from_process(replace(self._process_template, imager=self._base_imager, resist=new.resist))
            else:
                params = copy.deepcopy(self.params)
        new.params = params
        if cost is not None:
            if cost not in ("eq7", "eq8"):
                raise ValueError("cost must be 'eq7' or 'eq8'")
            new.mode = cost
        if wt_space is not None:
            new.wt_space = float(wt_space)
        new._check_params_match()
        new._refresh_scales()
        new._optics_names = [p.name for p in new.params.params if p.group == "optics"]
        if self._optics_names or new._optics_names:
            new._aerial, new._optics_key, new._imager = {}, None, self._base_imager
        new.n_evals = 0
        return new

    def _refresh_scales(self) -> None:
        self._scales = cost_scales([g.kind for g in self.gauges], self.gauges.weights(), self.mode, self.wt_space)

    # ---------------------------------------------------------------- internals
    def _check_params_match(self) -> None:
        rn = [p.name for p in self.params.params if p.group == "resist"]
        if rn != list(self.resist.param_names()):
            raise ValueError("parameter set does not match the resist model's parameters "
                             f"({rn} vs {list(self.resist.param_names())}); build it with ParameterSet.from_process")

    def _imaging(self, vals: Mapping[str, float]) -> Imager:
        key = tuple(vals[n] for n in self._optics_names)
        if key != self._optics_key:
            self._optics_key = key
            self._aerial.clear()
            self._imager = replace(self._base_imager, _kcache={}, **dict(zip(self._optics_names, key))) if key else self._base_imager
        return self._imager

    def _aerial_image(self, pid: str, imager: Imager) -> np.ndarray:
        I = self._aerial.get(pid)
        if I is None:
            I = imager.image(self.patterns[pid], self.grids[pid], tone=self.tone, boundary="padded", supersample=self.supersample)
            self._aerial[pid] = I
        return I

    def _measure(self, g: Gauge, F: np.ndarray, grid: Grid, level: float) -> float:
        d = g.unit
        p = np.array([g.x, g.y])
        if g.kind == "EP":
            return edge_placement(F, grid, p, d, level, search_nm=0.5 * g.cut_span())
        half = 0.5 * g.cut_span()
        feat = self._feature if g.kind == "CD" else ("bright" if self._feature == "dark" else "dark")
        return measure_cd(F, grid, p - half * d, p + half * d, level, feat)

    # ---------------------------------------------------------------- evaluation
    def evaluate(self, full_values: Optional[Sequence[float]] = None) -> Evaluation:
        """Simulate every gauge for the full parameter vector (default: the current starting values)."""
        v = self.params.values() if full_values is None else np.asarray(full_values, float)
        vals = self.params.as_dict(v)
        rv = [vals[p.name] for p in self.params.params if p.group == "resist"]
        if rv:
            self.resist.set_vector(rv)
        imager = self._imaging(vals)
        sim = np.full(len(self.gauges), np.nan)
        by_pat: Dict[str, List[int]] = {}
        for i, g in enumerate(self.gauges):
            by_pat.setdefault(g.pattern, []).append(i)
        for pid, idx in by_pat.items():
            grid = self.grids[pid]
            F, level = self.resist.field(self._aerial_image(pid, imager), grid.pixel)
            for i in idx:
                sim[i] = self._measure(self.gauges[i], F, grid, level)
        self.n_evals += 1
        failed = ~np.isfinite(sim)
        meas = self.gauges.measured()
        err = np.where(failed, self.nan_penalty, sim - meas)
        res = self._scales * err
        w = self.gauges.weights()
        kinds = np.array([g.kind for g in self.gauges])
        rk: Dict[str, float] = {}
        for k in ("CD", "SPACE", "EP"):
            sel = kinds == k
            W = w[sel].sum()
            rk[k] = float(np.sqrt(np.sum(w[sel] * err[sel] ** 2) / W)) if sel.any() and W > 0 else float("nan")
        return Evaluation(vals, sim, err, failed, res, float(np.sqrt(np.sum(res * res))), rk, self.mode)

    def residuals(self, x_free: Sequence[float]) -> np.ndarray:
        """Residual vector for the FREE parameters (least_squares objective)."""
        return self.evaluate(self.params.full_from_free(x_free)).residuals

    def cost_value(self, x_free: Sequence[float]) -> float:
        """Scalar cost for the FREE parameters (differential_evolution objective)."""
        return self.evaluate(self.params.full_from_free(x_free)).cost

    # ---------------------------------------------------------------- results
    def calibrated_process(self, values: Optional[Mapping[str, float]] = None) -> ProcessModel:
        """A new ProcessModel with the given parameter values (default: the problem's starting values): copied resist with the
        fitted resist parameters and an Imager copy with the fitted optical parameters. Usable directly by the OPC engine."""
        vals = dict(self.params.as_dict()) if values is None else {**self.params.as_dict(), **dict(values)}
        resist = copy.deepcopy(self.resist)
        rv = [vals[p.name] for p in self.params.params if p.group == "resist"]
        if rv:
            resist.set_vector(rv)
        kw = {n: vals[n] for n in self._optics_names}
        imager = replace(self._base_imager, _kcache={}, **kw) if kw else self._base_imager
        t = self._process_template
        return ProcessModel(imager, resist, t.tone, t.pixel, t.margin, t.supersample)
