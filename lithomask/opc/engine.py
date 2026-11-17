"""OPC entry point: correct(target, process_model, options) -> OPCResult (with a corrected Library).

options.stages selects what runs: optionally 'sraf' (assist features, placed first and kept fixed), then exactly one of
'rule' | 'model' | 'inverse' (default 'model').
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Union

from ..geometry import Polygon
from ..layout import Cell, Library
from .common import OPCOptions, OPCResult, ProcessModel, epe_stats, mrc_check
from .fragments import fragment_polygons
from .model import evaluate_epe, model_opc
from .rule import rule_opc

MAIN_STAGES = ("rule", "model", "inverse")


def _target_polygons(target: Union[Library, Cell, Sequence[Polygon]]) -> List[Polygon]:
    if isinstance(target, (Library, Cell)):
        return target.flatten()
    return list(target)


def measure_print_epe(target: Sequence[Polygon], mask: Sequence[Polygon], pm: ProcessModel, opts: OPCOptions) -> dict:
    """EPE statistics (rms / max / mean / n, nm) of `mask` against `target`, on the standard fragment sample points."""
    fl = fragment_polygons(target, opts.fragments)
    grid = pm.grid_for(list(target) + list(mask))
    return epe_stats(evaluate_epe(pm, mask, grid, fl, opts.model.search))


def correct(target: Union[Library, Cell, Sequence[Polygon]], process_model: ProcessModel,
            options: Optional[OPCOptions] = None) -> OPCResult:
    """Correct `target` (the shapes that should PRINT) so that the simulated print matches it."""
    opts = options or OPCOptions()
    polys = _target_polygons(target)
    if not polys:
        raise ValueError("target is empty")
    main = [s for s in opts.stages if s in MAIN_STAGES] or ["model"]
    bad = [s for s in opts.stages if s not in MAIN_STAGES + ("sraf",)]
    if bad or len(main) != 1:
        raise ValueError(f"stages must be optional 'sraf' plus exactly one of {MAIN_STAGES}; got {opts.stages}")
    stage = main[0]
    pm = process_model
    srafs: List[Polygon] = []
    if "sraf" in opts.stages:
        from .sraf import sraf_stage, verify_srafs        # noqa: F401
        srafs = sraf_stage(polys, pm, opts)
    log: List[dict] = []
    assists: List[Polygon] = []
    if stage == "rule":
        corrected = rule_opc(polys, opts)
        before = measure_print_epe(polys, list(polys) + srafs, pm, opts)
        after = measure_print_epe(polys, corrected + srafs, pm, opts)
        log.append({"stage": "rule", "iter": 0, "rms": after["rms"], "max": after["max"]})
    elif stage == "model":
        corrected, log, before, after, _, _ = model_opc(polys, pm, opts, extra_mask=srafs)
    else:
        from .inverse import inverse_opc
        corrected, assists, log, before, after = inverse_opc(polys, pm, opts, extra_mask=srafs)
    if srafs and opts.sraf.check_print:
        from .sraf import Bar  # noqa: F401
        # bars were checked against the uncorrected target; re-check against the corrected main mask
        srafs = _recheck(srafs, corrected + assists, pm, opts)
        after = measure_print_epe(polys, corrected + assists + srafs, pm, opts)
    all_srafs = assists + srafs
    lib = Library(name="OPC")
    cell = lib.new_cell("OPC_TOP")
    cell.add(corrected)
    cell.add(all_srafs)
    return OPCResult(polygons=corrected, srafs=all_srafs, log=log, epe_before=before, epe_after=after, library=lib,
                     mrc=mrc_check(corrected + all_srafs, opts.mrc.min_width, opts.mrc.min_space), stage_used=stage)


def _recheck(srafs: List[Polygon], main: Sequence[Polygon], pm: ProcessModel, opts: OPCOptions) -> List[Polygon]:
    """Drop assist polygons that print next to the corrected main mask (no narrowing: they are already polygons)."""
    from ..optics import Grid  # noqa: F401
    from .common import value_at
    import numpy as np
    polys = list(main) + list(srafs)
    grid = pm.grid_for(polys)
    I = pm.imager.image(polys, grid, tone=pm.tone, supersample=pm.supersample)
    F, lv = pm.resist.field(I, grid.pixel)
    keep = []
    for s in srafs:
        c = np.asarray(s.outer)
        x0, y0, x1, y1 = s.bbox
        n = 9
        if (x1 - x0) >= (y1 - y0):
            pts = np.column_stack([np.linspace(x0, x1, n), np.full(n, 0.5 * (y0 + y1))])
        else:
            pts = np.column_stack([np.full(n, 0.5 * (x0 + x1)), np.linspace(y0, y1, n)])
        v = value_at(F, grid, pts[:, 0], pts[:, 1])
        if not np.any(pm.inside_feature(v, lv)):
            keep.append(s)
    return keep
