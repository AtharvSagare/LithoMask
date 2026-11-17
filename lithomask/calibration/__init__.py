"""Phase 6 - model calibration: fit resist (and optionally optical) parameters to measured gauges.

Typical use
    gauges   = GaugeSet.from_csv("wafer_gauges.csv")                 # pattern id, type, x, y, direction, measured, weight
    patterns = patterns_from_library(Library.read_gds("test_patterns.gds"))   # {pattern id: [Polygon]}
    process  = ProcessModel.anchored(imager, cd=180)                  # starting model (any ResistModel works)
    params   = ParameterSet.from_process(process, optics=("na",), fixed=["k_max"], bounds={"t0": (0.1, 0.6)})
    problem  = CalibrationProblem(process, patterns, gauges, params, cost="eq8", wt_space=5.0)
    result   = calibrate(problem, OptimizeOptions(method="de+ls"))
    write_report(problem, result, "out")                              # summary txt + residual csv + png
    fitted   = problem.calibrated_process(result.values)             # -> feed to lithomask.opc.correct(...)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..geometry import Polygon
from .gauges import Gauge, GaugeSet, KINDS
from .model import (CalibrationProblem, Evaluation, Param, ParameterSet, combine_cost, cost_scales)
from .optimize import CalibrationResult, OptimizeOptions, calibrate
from .report import plot_report, residual_rows, summary_text, write_report, write_residuals_csv
from .synthetic import line_array, standard_patterns, synthesize_measurements


def patterns_from_library(lib, cells: Optional[Sequence[str]] = None) -> Dict[str, List[Polygon]]:
    """{cell name: flattened polygons}; the cell name is the pattern id used in the gauge CSV. Default: all top-level cells."""
    names = list(cells) if cells is not None else [c.name for c in lib.top_cells()]
    return {n: lib.flatten(n) for n in names}


__all__ = ["Gauge", "GaugeSet", "KINDS", "CalibrationProblem", "Evaluation", "Param", "ParameterSet", "combine_cost",
           "cost_scales", "CalibrationResult", "OptimizeOptions", "calibrate", "plot_report", "residual_rows", "summary_text",
           "write_report", "write_residuals_csv", "line_array", "standard_patterns", "synthesize_measurements",
           "patterns_from_library"]
