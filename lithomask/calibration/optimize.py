"""Phase 6 - calibration optimisers.

calibrate(problem, options) runs, on the FREE parameters of problem.params only:
  stage 'de' : scipy differential_evolution (genetic-style global search) on the scalar cost (Eq.7 / Eq.8); the starting vector is
               injected into the initial population so the result is never worse than the start.
  stage 'ls' : scipy least_squares (trust-region reflective, bounded) on the residual vector, started from the best point so far;
               its Jacobian gives approximate 1-sigma parameter uncertainties  cov = s^2 (J^T J)^+ ,  s^2 = |r|^2 / (m - n)
               (r = cost residuals, so the numbers are indicative, not rigorous: weights and the contour-based measurements are
               not Gaussian-noise ideal).
method = 'de+ls' (default), 'de' or 'ls'. Parameters marked fixed in the ParameterSet are never touched. Bounds are hard.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import differential_evolution, least_squares

from .model import CalibrationProblem, Evaluation


@dataclass
class OptimizeOptions:
    method: str = "de+ls"                 # 'de+ls' | 'de' | 'ls'
    de_popsize: int = 12                  # population = popsize * n_free
    de_maxiter: int = 60
    de_tol: float = 1e-4
    de_atol: float = 1e-6                 # nm
    de_seed: Optional[int] = 0
    de_strategy: str = "best1bin"
    de_mutation: Tuple[float, float] = (0.5, 1.0)
    de_recombination: float = 0.7
    ls_max_nfev: int = 100
    ls_diff_step: float = 1e-3            # relative finite-difference step (measurements are piecewise smooth: avoid tiny steps)
    ls_loss: str = "linear"               # 'linear' | 'soft_l1' | 'huber' ... (robust losses downweight outlier gauges)
    ls_ftol: float = 1e-10
    ls_xtol: float = 1e-10
    ls_gtol: float = 1e-10
    progress: Optional[Callable[[str, int, float], None]] = None    # (stage, n_evals, best_cost)


@dataclass
class CalibrationResult:
    names: List[str]                          # all parameters
    start: Dict[str, float]
    values: Dict[str, float]                  # fitted (fixed ones unchanged)
    free_names: List[str]
    bounds: Dict[str, Tuple[float, float]]
    at_bound: List[str]                       # free parameters that ended on a bound (check the bounds / identifiability)
    param_std: Dict[str, float]               # approximate 1-sigma, free parameters, NaN if unavailable
    correlated: List[Tuple[str, str, float]]  # free-parameter pairs with |correlation| > 0.9 (poorly separable by the gauges)
    start_eval: Evaluation
    final_eval: Evaluation
    history: List[Tuple[str, int, float]]     # (stage, n_evals, best cost) at every improvement
    n_evals: int
    runtime_s: float
    method: str
    success: bool
    message: str


class _Tracker:
    """Wraps the objective: counts evaluations, remembers the best point, logs improvements."""

    def __init__(self, problem: CalibrationProblem, stage: str, history: list, progress):
        self.p, self.stage, self.history, self.progress = problem, stage, history, progress
        self.best_x: Optional[np.ndarray] = None
        self.best_cost = np.inf
        self.n0 = problem.n_evals

    def _note(self, x, cost):
        if cost < self.best_cost:
            self.best_cost, self.best_x = float(cost), np.array(x, float)
            n = self.p.n_evals
            self.history.append((self.stage, n, self.best_cost))
            if self.progress is not None:
                self.progress(self.stage, n, self.best_cost)

    def scalar(self, x):
        c = self.p.cost_value(x)
        self._note(x, c)
        return c

    def vector(self, x):
        r = self.p.residuals(x)
        self._note(x, float(np.sqrt(np.sum(r * r))))
        return r


def calibrate(problem: CalibrationProblem, options: Optional[OptimizeOptions] = None) -> CalibrationResult:
    o = options or OptimizeOptions()
    if o.method not in ("de+ls", "de", "ls"):
        raise ValueError("method must be 'de+ls', 'de' or 'ls'")
    t0 = time.time()
    ps = problem.params
    start_vals = ps.as_dict()
    start_eval = problem.evaluate()
    free = ps.free
    history: List[Tuple[str, int, float]] = [("start", problem.n_evals, start_eval.cost)]
    x = ps.free_values()
    lb, ub = ps.free_bounds()
    std = {n: float("nan") for n in ps.free_names}
    correlated: List[Tuple[str, str, float]] = []
    success, message = True, "no free parameters"
    if len(free) > 0:
        best_x, best_cost = x.copy(), start_eval.cost
        if o.method in ("de", "de+ls"):
            tr = _Tracker(problem, "de", history, o.progress)
            r = differential_evolution(
                tr.scalar, list(zip(lb, ub)), x0=x, strategy=o.de_strategy, maxiter=o.de_maxiter, popsize=o.de_popsize,
                tol=o.de_tol, atol=o.de_atol, mutation=o.de_mutation, recombination=o.de_recombination, seed=o.de_seed,
                polish=False, init="latinhypercube", updating="immediate", workers=1)
            if tr.best_x is not None and tr.best_cost <= best_cost:
                best_x, best_cost = tr.best_x, tr.best_cost
            success, message = bool(r.success), f"de: {r.message}"
        if o.method in ("ls", "de+ls"):
            tr = _Tracker(problem, "ls", history, o.progress)
            x0 = np.clip(best_x, lb, ub)
            ls = least_squares(
                tr.vector, x0, bounds=(lb, ub), method="trf", x_scale="jac", diff_step=o.ls_diff_step, loss=o.ls_loss,
                max_nfev=o.ls_max_nfev, ftol=o.ls_ftol, xtol=o.ls_xtol, gtol=o.ls_gtol)
            if tr.best_x is not None and tr.best_cost <= best_cost:
                best_x, best_cost = tr.best_x, tr.best_cost
            success = bool(ls.success)         # the refinement stage decides; DE usually just stops at its iteration cap
            message = (message + "; " if o.method == "de+ls" else "") + f"ls: {ls.message}"
            try:
                m, n = ls.jac.shape
                s2 = float(np.sum(ls.fun ** 2)) / max(m - n, 1)
                cov = s2 * np.linalg.pinv(ls.jac.T @ ls.jac)
                sd = np.sqrt(np.maximum(np.diag(cov), 0.0))
                std = {nm: float(sd[i]) for i, nm in enumerate(ps.free_names)}
                nm_ = ps.free_names
                for i in range(len(nm_)):
                    for j in range(i + 1, len(nm_)):
                        if sd[i] > 0 and sd[j] > 0:
                            r = float(cov[i, j] / (sd[i] * sd[j]))
                            if abs(r) > 0.9:
                                correlated.append((nm_[i], nm_[j], r))
            except Exception:                           # pragma: no cover - degenerate Jacobian
                pass
        x = best_x
    full = ps.full_from_free(x) if len(free) else ps.values()
    final = problem.evaluate(full)
    at_bound = [p.name for p, xi in zip(free, x)
                if (xi - p.lower) < 1e-6 * (p.upper - p.lower) or (p.upper - xi) < 1e-6 * (p.upper - p.lower)]
    return CalibrationResult(
        names=ps.names, start=start_vals, values=ps.as_dict(full), free_names=ps.free_names,
        bounds={p.name: (p.lower, p.upper) for p in ps.params}, at_bound=at_bound, param_std=std,
        correlated=correlated,
        start_eval=start_eval, final_eval=final, history=history, n_evals=problem.n_evals, runtime_s=time.time() - t0,
        method=o.method, success=success, message=message)
