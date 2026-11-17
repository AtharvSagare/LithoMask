"""Phase 6 - calibration reporting: per-gauge residual table (CSV), text summary and plots (PNG).

All residuals are  simulated - measured  in nm. 'failed' gauges (no printable feature / contour in range) carry the penalty value
(CalibrationProblem.nan_penalty) as their error and are listed explicitly.
"""
from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from .gauges import KINDS
from .model import CalibrationProblem, Evaluation
from .optimize import CalibrationResult


def residual_rows(problem: CalibrationProblem, ev: Evaluation) -> List[Dict[str, object]]:
    rows = []
    for i, g in enumerate(problem.gauges):
        rows.append({"index": i, "pattern": g.pattern, "type": g.kind, "x": g.x, "y": g.y, "direction": g.direction,
                     "weight": g.weight, "measured": g.measured,
                     "simulated": float("nan") if ev.failed[i] else float(ev.simulated[i]),
                     "error": float(ev.errors[i]), "failed": bool(ev.failed[i]), "id": g.id})
    return rows


def write_residuals_csv(problem: CalibrationProblem, ev: Evaluation, path: str) -> str:
    rows = residual_rows(problem, ev)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()})
    return path


def _fmt(x: float, nd: int = 4) -> str:
    return "nan" if not np.isfinite(x) else f"{x:.{nd}g}"


def summary_text(problem: CalibrationProblem, res: CalibrationResult, worst: int = 5) -> str:
    cm = "Eq.7 (weighted RMS)" if problem.mode == "eq7" else f"Eq.8 (pattern-shift-aware, wt_space={problem.wt_space:g})"
    c = problem.gauges.counts()
    L = ["LithoMask calibration report", "=" * 27,
         f"cost            : {cm}",
         f"gauges          : {len(problem.gauges)}  (CD {c['CD']}, SPACE {c['SPACE']}, EP {c['EP']}) on "
         f"{len(problem.gauges.pattern_ids())} pattern(s)",
         f"method          : {res.method}   evaluations: {res.n_evals}   time: {res.runtime_s:.1f} s",
         f"status          : {'ok' if res.success else 'check'} - {res.message}",
         f"cost  start -> final : {_fmt(res.start_eval.cost)} -> {_fmt(res.final_eval.cost)} nm", "",
         "RMS per gauge type (nm, weighted, start -> final)"]
    for k in KINDS:
        a, b = res.start_eval.rms_by_kind[k], res.final_eval.rms_by_kind[k]
        if np.isfinite(a) or np.isfinite(b):
            L.append(f"  {k:<6}{_fmt(a)} -> {_fmt(b)}")
    nf0, nf1 = int(res.start_eval.failed.sum()), int(res.final_eval.failed.sum())
    L += [f"failed measurements start -> final: {nf0} -> {nf1}", "", "Parameters",
          f"  {'name':<14}{'start':>12}{'fitted':>12}{'+-1sigma':>11}{'lower':>10}{'upper':>10}  note"]
    for n in res.names:
        lo, hi = res.bounds[n]
        note = "fixed" if n not in res.free_names else ("AT BOUND" if n in res.at_bound else "")
        L.append(f"  {n:<14}{_fmt(res.start[n], 5):>12}{_fmt(res.values[n], 5):>12}"
                 f"{_fmt(res.param_std.get(n, float('nan')), 3):>11}{_fmt(lo, 4):>10}{_fmt(hi, 4):>10}  {note}")
    if res.correlated:
        L += ["", "NOTE - strongly correlated parameter pairs (|r| > 0.9): they move together, so each one alone is determined less",
              "well than their combination; judge the individual values by the +-1sigma column."]
        L += [f"  {a} ~ {b}: r = {r:+.3f}" for a, b, r in res.correlated]
    big = [n for n in res.free_names if np.isfinite(res.param_std.get(n, np.nan)) and
           res.param_std[n] > 0.2 * max(abs(res.values[n]), 1e-12)]
    if big:
        L += ["", "WARNING - uncertainty (+-1sigma) exceeds 20% of the fitted value for: " + ", ".join(big),
              "  these parameters are poorly determined by the gauges (try fixing one of them, or add gauges on patterns that",
              "  respond differently to them); other parameter sets may fit the data about as well."]
    ev = res.final_eval
    order = np.argsort(-np.abs(ev.errors))[:worst]
    L += ["", f"Largest residuals (final, simulated - measured, nm)"]
    for i in order:
        g = problem.gauges[int(i)]
        L.append(f"  {g.label():<40}{g.kind:<6}meas {g.measured:9.3f}  err {ev.errors[i]:+9.3f}{'  FAILED' if ev.failed[i] else ''}")
    return "\n".join(L) + "\n"


def plot_report(problem: CalibrationProblem, res: CalibrationResult, path: str) -> str:
    """2 x 2 figure: simulated vs measured (start & final), residuals per gauge, residual histogram, convergence."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"CD": "tab:blue", "SPACE": "tab:orange", "EP": "tab:green"}
    meas = problem.gauges.measured()
    kinds = np.array([g.kind for g in problem.gauges])
    fig, ax = plt.subplots(2, 2, figsize=(11, 8.5))
    # (a) sim vs measured
    a = ax[0, 0]
    for k in KINDS:
        s = kinds == k
        if not s.any():
            continue
        a.scatter(meas[s], res.start_eval.simulated[s], s=14, facecolors="none", edgecolors=colors[k], alpha=0.6)
        a.scatter(meas[s], res.final_eval.simulated[s], s=14, color=colors[k], label=f"{k} (n={int(s.sum())})")
    lim = [np.nanmin(np.r_[meas, res.final_eval.simulated]), np.nanmax(np.r_[meas, res.final_eval.simulated])]
    a.plot(lim, lim, "k-", lw=0.7)
    a.set(xlabel="measured (nm)", ylabel="simulated (nm)", title="simulated vs measured (open = start, filled = final)")
    a.legend(fontsize=8)
    # (b) residual per gauge
    b = ax[0, 1]
    idx = np.arange(len(meas))
    for k in KINDS:
        s = kinds == k
        if s.any():
            b.plot(idx[s], res.start_eval.errors[s], "o", mfc="none", color=colors[k], ms=4, alpha=0.6)
            b.plot(idx[s], res.final_eval.errors[s], "o", color=colors[k], ms=4)
    b.axhline(0, color="k", lw=0.7)
    b.set(xlabel="gauge index", ylabel="simulated - measured (nm)", title="residuals per gauge")
    # (c) histogram
    c = ax[1, 0]
    e0, e1 = res.start_eval.errors, res.final_eval.errors
    lo, hi = float(min(e0.min(), e1.min())), float(max(e0.max(), e1.max()))
    bins = np.linspace(lo, hi if hi > lo else lo + 1.0, 25)
    c.hist(e0, bins=bins, alpha=0.45, label=f"start (cost {_fmt(res.start_eval.cost, 3)})")
    c.hist(e1, bins=bins, alpha=0.65, label=f"final (cost {_fmt(res.final_eval.cost, 3)})")
    c.set(xlabel="error (nm)", ylabel="gauges", title="residual distribution")
    c.legend(fontsize=8)
    # (d) convergence
    d = ax[1, 1]
    h = res.history
    if len(h) > 1:
        n = np.array([t[1] for t in h])
        v = np.array([t[2] for t in h])
        d.semilogy(n, np.maximum(v, 1e-9), "-o", ms=2)
        for st in ("de", "ls"):
            first = [t[1] for t in h if t[0] == st]
            if first:
                d.axvline(first[0], color="gray", ls=":", lw=0.8)
                d.text(first[0], d.get_ylim()[1], f" {st}", va="top", fontsize=8)
    d.set(xlabel="cost evaluations", ylabel="best cost (nm)", title="convergence")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def write_report(problem: CalibrationProblem, res: CalibrationResult, outdir: str, stem: str = "calibration") -> Dict[str, str]:
    """Write <stem>_summary.txt, <stem>_residuals.csv and <stem>_report.png into outdir; returns the paths."""
    os.makedirs(outdir, exist_ok=True)
    paths = {"summary": os.path.join(outdir, f"{stem}_summary.txt"),
             "residuals": os.path.join(outdir, f"{stem}_residuals.csv"),
             "plot": os.path.join(outdir, f"{stem}_report.png")}
    with open(paths["summary"], "w", encoding="utf-8") as fh:
        fh.write(summary_text(problem, res))
    write_residuals_csv(problem, res.final_eval, paths["residuals"])
    plot_report(problem, res, paths["plot"])
    return paths
