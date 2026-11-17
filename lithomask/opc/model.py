"""Stage B - model-based OPC: edge fragmentation + iterative EPE correction.

Loop (all lengths nm):
  1. fragment the target edges (fragments.fragment_polygons);
  2. simulate the current mask, measure EPE at every fragment midpoint along its outward normal (common.measure_epe);
  3. update  delta_f <- delta_f - gain * EPE_f   (gain ~ 1/MEEF), then apply the mask limits:
       |step| < min_step -> 0 (no dither), |step| <= max_step, |delta| <= max_bias, snap to the mask grid,
       |delta_f - delta_g| <= max_jog for neighbouring fragments f, g of the same edge;
  4. stop when max|EPE| < tol, after max_iter iterations, or when the RMS has not improved for STALL iterations.
The best iterate (lowest RMS) is returned, so a diverging run never leaves a worse mask than the uncorrected one.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..geometry import Polygon
from ..optics import Grid
from .common import MRCOptions, OPCOptions, ProcessModel, epe_stats, measure_epe
from .fragments import FragmentedLayout, fragment_polygons

STALL = 4


def evaluate_epe(pm: ProcessModel, mask: Sequence[Polygon], grid: Grid, fl: FragmentedLayout, search: float) -> np.ndarray:
    """EPE (nm) of the simulated print of `mask` at every fragment midpoint of `fl`."""
    I = pm.imager.image(list(mask), grid, tone=pm.tone, supersample=pm.supersample)
    F, level = pm.resist.field(I, grid.pixel)
    return measure_epe(F, level, grid, fl.midpoints(), fl.normals(), pm.inside_feature, search)


def limit_jogs(fl: FragmentedLayout, d: np.ndarray, max_jog: float, passes: int = 4) -> np.ndarray:
    """Clip shifts so neighbouring fragments of one edge differ by at most max_jog."""
    d = d.copy()
    for _ in range(passes):
        for fr in fl.edge_frags:
            for i in range(1, len(fr)):
                d[fr[i]] = np.clip(d[fr[i]], d[fr[i - 1]] - max_jog, d[fr[i - 1]] + max_jog)
            for i in range(len(fr) - 2, -1, -1):
                d[fr[i]] = np.clip(d[fr[i]], d[fr[i + 1]] - max_jog, d[fr[i + 1]] + max_jog)
    return d


def apply_limits(fl: FragmentedLayout, d_old: np.ndarray, step: np.ndarray, mrc: MRCOptions) -> np.ndarray:
    step = np.where(np.abs(step) < mrc.min_step, 0.0, step)
    step = np.clip(step, -mrc.max_step, mrc.max_step)
    d = np.clip(d_old + step, -mrc.max_bias, mrc.max_bias)
    if mrc.grid > 0:
        d = np.round(d / mrc.grid) * mrc.grid
    return limit_jogs(fl, d, mrc.max_jog)


def model_opc(target: Sequence[Polygon], pm: ProcessModel, opts: OPCOptions, extra_mask: Sequence[Polygon] = ()):
    """Run the loop. Returns (mask polygons, log, epe_before stats, epe_after stats, FragmentedLayout, best deltas).
    `extra_mask` (e.g. SRAFs) is added to the mask in every simulation but never moved."""
    mo, mrc = opts.model, opts.mrc
    fl = fragment_polygons(target, opts.fragments)
    grid = pm.grid_for(list(target) + list(extra_mask))
    d = np.zeros(len(fl.frags))
    log: List[dict] = []
    best_rms, best_d, best_it, before = np.inf, d.copy(), 0, None
    for it in range(mo.max_iter + 1):
        mask = fl.build(d) + list(extra_mask)
        epe = evaluate_epe(pm, mask, grid, fl, mo.search)
        st = epe_stats(epe)
        if before is None:
            before = st
        log.append({"stage": "model", "iter": it, "rms": st["rms"], "max": st["max"]})
        if st["rms"] < best_rms:
            best_rms, best_d, best_it, best_st = st["rms"], d.copy(), it, st
        if opts.progress is not None and opts.progress("model", it, st["rms"], st["max"]) is False:
            break
        if st["max"] < mo.tol or it == mo.max_iter or it - best_it >= STALL:
            break
        d = apply_limits(fl, d, -mo.gain * epe, mrc)
    return fl.build(best_d), log, before, best_st, fl, best_d
