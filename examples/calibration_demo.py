"""Phase 6 demo: calibrate a compact resist model (threshold + diffusion blur length) from 'wafer' gauges, using the real file workflow.

  1. synthetic wafer data are generated from a known truth model (+1 nm measurement noise) and written as
       examples/calibration_patterns.gds   (one cell per pattern id)   and   examples/calibration_gauges.csv
  2. the files are read back exactly as real measurement files would be (GaugeSet.from_csv, Library.read_gds)
  3. a perturbed starting model is calibrated (differential evolution -> least-squares refinement, Eq.8 cost)
  4. summary text, per-gauge residual CSV and a PNG report are written to examples/calibration_out/

Replace step 1 by your own CSV (columns: pattern id, type, x, y, direction, measured value, weight) and GDS to calibrate for real.
Which parameters can be fitted depends on the gauges: threshold + blur length separate well on dense / isolated / line-end gauges,
whereas e.g. VariableThreshold t0 together with k_slope (or k_max) are nearly degenerate on line/space data -- the report warns
when the +-1sigma uncertainty says so.
Run:  python examples\\calibration_demo.py
"""
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lithomask.calibration import (CalibrationProblem, GaugeSet, OptimizeOptions, ParameterSet, calibrate, patterns_from_library,
                                 standard_patterns, summary_text, synthesize_measurements, write_report)
from lithomask.layout import Library
from lithomask.opc.common import ProcessModel
from lithomask.optics import Imager, Source
from lithomask.resist import CompactResist, Term

HERE = os.path.dirname(os.path.abspath(__file__))
GDS, CSV, OUT = (os.path.join(HERE, "calibration_patterns.gds"), os.path.join(HERE, "calibration_gauges.csv"),
                 os.path.join(HERE, "calibration_out"))


def blur_model(threshold: float, sigma_nm: float) -> CompactResist:
    """R = I (x) Gaussian(sigma): one compact-model term whose blur length is a fit parameter."""
    return CompactResist([Term(1.0, 0, 0.0, 1, 0, sigma_nm, ("sigma",))], threshold)


def main():
    imager = Imager(wavelength=193.0, na=0.75, source=Source.conventional(0.6))
    anchor = ProcessModel.anchored(imager, cd=180.0, pixel=12.0)
    t0 = anchor.resist.threshold
    patterns, template = standard_patterns(180.0, pitches=(360.0, 450.0, 600.0, 900.0, 1500.0))

    # ---- 1. synthetic 'wafer' data from a hidden truth model: threshold t0 + 0.02, resist blur 35 nm
    truth_problem = CalibrationProblem(replace(anchor, resist=blur_model(t0, 35.0)), patterns, template)
    truth = {"threshold": t0 + 0.02, "t0.sigma": 35.0}
    gauges = synthesize_measurements(truth_problem, truth, noise_nm=1.0, seed=11)
    lib = Library("CALIB")
    for pid, polys in patterns.items():
        lib.new_cell(pid).add(*polys)
    lib.write_gds(GDS)
    gauges.to_csv(CSV)
    print(f"wrote {GDS}\nwrote {CSV}  ({len(gauges)} gauges: {gauges.counts()})")

    # ---- 2. read them back like real files
    gauges = GaugeSet.from_csv(CSV)
    patterns = patterns_from_library(Library.read_gds(GDS))

    # ---- 3. calibrate from a perturbed start (threshold -0.03, blur 15 nm instead of 35 nm)
    start_proc = replace(anchor, resist=blur_model(t0 - 0.03, 15.0))
    params = ParameterSet.from_process(start_proc)
    problem = CalibrationProblem(start_proc, patterns, gauges, params, cost="eq8", wt_space=3.0)
    result = calibrate(problem, OptimizeOptions(method="de+ls", de_maxiter=15, de_popsize=8))

    # ---- 4. report
    paths = write_report(problem, result, OUT, "blur_model")
    print(summary_text(problem, result))
    print("truth:", {k: round(v, 4) for k, v in truth.items()})
    print("files:", *paths.values(), sep="\n  ")
    fitted = problem.calibrated_process(result.values)       # ready for lithomask.opc.correct(target, fitted, options)
    print("calibrated ProcessModel:", type(fitted.resist).__name__, fitted.resist.get_vector().round(4))


if __name__ == "__main__":
    main()
