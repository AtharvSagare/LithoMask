"""Phase 6 tests: gauges + CSV import, Eq.7/Eq.8 cost, parameter handling, synthetic-data parameter recovery, reports.

The imaging is the slow part, so one module-level problem is imaged once and the variants share its aerial images (derive()).
"""
import math
import os

import numpy as np
import pytest

from dataclasses import replace

from lithomask import geometry as g
from lithomask.calibration import (CalibrationProblem, Gauge, GaugeSet, OptimizeOptions, ParameterSet, calibrate, combine_cost,
                                 cost_scales, patterns_from_library, standard_patterns, summary_text, synthesize_measurements,
                                 write_report)
from lithomask.calibration.gauges import normalize_kind, parse_direction
from lithomask.layout import Library
from lithomask.opc.common import ProcessModel
from lithomask.optics import Imager, Source
from lithomask.resist import CompactResist, Term, VariableThreshold


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def base():
    im = Imager(wavelength=193.0, na=0.75, source=Source.conventional(0.6))
    proc = ProcessModel.anchored(im, cd=180.0, pixel=12.0)
    pats, tmpl = standard_patterns(180.0, pitches=(360.0, 600.0, 1500.0))
    prob = CalibrationProblem(proc, pats, tmpl)
    prob.evaluate()                                  # images every pattern once
    return proc, prob


# ---------------------------------------------------------------- gauges / CSV
CSV_TEXT = """# comment line
Pattern ID, Type, X, Y, Direction, Measured Value, Weight
dense_360, CD, 0, 0, x, 171.5, 1
dense_360, space, 180, 0, H, 188.0, 2
iso_1500, EP, 90, 0, 0, -1.25,
lineend, epe, 0, 600, 90, -5.0, 0.5
"""


def test_csv_import_aliases_defaults_and_roundtrip(tmp_path):
    gs = GaugeSet.from_csv(CSV_TEXT)
    assert len(gs) == 4 and gs.counts() == {"CD": 1, "SPACE": 1, "EP": 2}
    assert [x.kind for x in gs] == ["CD", "SPACE", "EP", "EP"]
    assert gs[0].direction == 0.0 and gs[1].direction == 0.0 and gs[3].direction == 90.0
    assert gs[2].weight == 1.0                       # blank weight -> 1
    assert gs[1].weight == 2.0 and gs[3].measured == -5.0
    assert gs.pattern_ids() == ["dense_360", "iso_1500", "lineend"]
    p = tmp_path / "g.csv"
    gs.to_csv(str(p))
    gs2 = GaugeSet.from_csv(str(p))
    assert [(a.pattern, a.kind, a.x, a.y, a.direction, a.measured, a.weight) for a in gs2] == \
           [(a.pattern, a.kind, a.x, a.y, a.direction, a.measured, a.weight) for a in gs]


def test_csv_errors_report_line_numbers_and_missing_columns():
    with pytest.raises(ValueError, match="missing required column"):
        GaugeSet.from_csv("pattern id,type,x,y\na,CD,0,0\n")
    with pytest.raises(ValueError, match="line 3"):
        GaugeSet.from_csv("pattern id,type,x,y,direction,measured value\na,CD,0,0,0,150\nb,CD,zero,0,0,150\n")
    with pytest.raises(ValueError, match="unknown gauge type"):
        GaugeSet.from_csv("pattern id,type,x,y,direction,measured value\na,BLOB,0,0,0,150\n")
    with pytest.raises(ValueError, match="weight"):
        Gauge("a", "CD", 0, 0, 0, 150, weight=-1)
    with pytest.raises(ValueError, match="empty gauge file"):
        GaugeSet.from_csv("# nothing\n\n")
    assert normalize_kind("Edge Placement") == "EP" and parse_direction("vertical") == 90.0 and parse_direction(" 45 ") == 45.0


# ---------------------------------------------------------------- cost functions (hand-computed)
def test_eq7_cost_matches_hand_value():
    e, w = [3.0, 4.0], [1.0, 3.0]
    assert combine_cost(e, ["CD", "CD"], w, "eq7") == pytest.approx(math.sqrt((1 * 9 + 3 * 16) / 4.0))


def test_eq8_cost_balances_space_gauges_and_residuals_are_consistent():
    kinds, w = ["CD", "CD", "EP", "SPACE", "SPACE"], [1.0, 1.0, 2.0, 2.0, 1.0]
    e = np.array([2.0, -2.0, 1.0, 3.0, -3.0])
    # CD/EP group: (1*4 + 1*4 + 2*1) / 4 = 2.5 ; SPACE group: (2*9 + 1*9) / 3 = 9 ; wt_space = 0.5 -> 4.5
    assert combine_cost(e, kinds, w, "eq8", 0.5) == pytest.approx(math.sqrt(2.5 + 0.5 * 9.0))
    r = cost_scales(kinds, w, "eq8", 0.5) * e
    assert np.sum(r ** 2) == pytest.approx(2.5 + 4.5)
    # no SPACE gauges: eq8 == eq7
    assert combine_cost(e[:3], kinds[:3], w[:3], "eq8", 7.0) == pytest.approx(combine_cost(e[:3], kinds[:3], w[:3], "eq7"))
    # zero-weight gauges drop out
    assert combine_cost([5.0, 1.0], ["CD", "CD"], [0.0, 1.0], "eq7") == pytest.approx(1.0)
    with pytest.raises(ValueError):
        cost_scales(["CD"], [1.0], "eq9")


# ---------------------------------------------------------------- parameters
def test_parameter_set_bounds_fixing_start_and_errors(base):
    proc, _ = base
    ps = ParameterSet.from_process(proc, bounds={"threshold": (0.1, 0.5)}, start={"threshold": 0.9})
    assert ps.names == ["threshold"] and ps.params[0].value == 0.5            # start clipped into the bounds
    vtr = ParameterSet.from_process(replace(proc, resist=VariableThreshold(t0=0.25)), fixed=["k_max", "k_min"])
    assert vtr.free_names == ["t0", "k_slope"]
    full = vtr.full_from_free([0.31, 0.02])
    assert full.tolist() == [0.31, 0.0, 0.0, 0.02]
    lb, ub = vtr.free_bounds()
    assert lb.shape == ub.shape == (2,)
    with pytest.raises(ValueError, match="unknown parameter"):
        ParameterSet.from_process(proc, fixed=["nope"])
    with pytest.raises(ValueError, match="unknown optical"):
        ParameterSet.from_process(proc, optics=("wavelength",))
    with pytest.raises(ValueError, match="upper must exceed"):
        ParameterSet.from_process(proc, bounds={"threshold": (0.5, 0.2)})


def test_problem_validates_patterns_and_flags_failed_measurements(base):
    proc, prob = base
    with pytest.raises(ValueError, match="unknown pattern"):
        CalibrationProblem(proc, {"a": [g.rectangle(0, 0, 10, 10)]}, [Gauge("b", "CD", 0, 0)])
    with pytest.raises(ValueError, match="does not have"):
        prob.derive(gauges=[Gauge("nope", "CD", 0, 0)])
    # a CD gauge in empty space: its midpoint is not inside a printed feature -> failed -> penalty, finite cost
    bad = GaugeSet([Gauge("iso_1500", "CD", 0, 0, 0, 180.0), Gauge("iso_1500", "CD", 400, 0, 0, 180.0)])
    ev = prob.derive(gauges=bad).evaluate()
    assert ev.failed.tolist() == [False, True]
    assert ev.errors[1] == pytest.approx(100.0) and np.isfinite(ev.cost)


# ---------------------------------------------------------------- synthetic recovery
def test_constant_threshold_recovery_from_perturbed_start(base):
    proc, prob = base
    thr0 = proc.resist.threshold
    truth = {"threshold": thr0 + 0.03}
    gauges = synthesize_measurements(prob, truth)
    assert len(gauges) >= 8 and {"CD", "SPACE", "EP"} <= {k for k in gauges.counts() if gauges.counts()[k] > 0}
    p = prob.derive(gauges, params=ParameterSet.from_process(proc, start={"threshold": thr0 - 0.05}))
    start_cost = p.evaluate().cost
    assert start_cost > 5.0                                              # a poor start (nm)
    res = calibrate(p, OptimizeOptions(method="de+ls", de_maxiter=15, de_popsize=8))
    assert res.values["threshold"] == pytest.approx(truth["threshold"], abs=2e-4)
    assert res.final_eval.cost < 0.05 and res.final_eval.cost < start_cost
    assert res.final_eval.rms_by_kind["CD"] < 0.1
    # least-squares alone also converges for this well-conditioned problem
    res_ls = calibrate(p.derive(), OptimizeOptions(method="ls"))
    assert res_ls.values["threshold"] == pytest.approx(truth["threshold"], abs=2e-4)


def test_variable_threshold_recovery_with_fixed_parameters(base):
    proc, prob = base
    thr0 = proc.resist.threshold
    vproc = replace(proc, resist=VariableThreshold(t0=thr0))
    vprob = prob.derive(resist=VariableThreshold(t0=thr0))
    truth = {"t0": thr0 + 0.02, "k_slope": 0.04}
    gauges = synthesize_measurements(vprob, truth)
    params = ParameterSet.from_process(vproc, fixed=["k_max", "k_min"], start={"t0": thr0 - 0.03, "k_slope": 0.0},
                                       bounds={"k_slope": (-0.2, 0.2)})
    p = vprob.derive(gauges, params=params)
    assert p.evaluate().cost > 3.0
    res = calibrate(p, OptimizeOptions(method="de+ls", de_maxiter=12, de_popsize=8))
    assert res.values["t0"] == pytest.approx(truth["t0"], abs=1e-3)
    assert res.values["k_slope"] == pytest.approx(truth["k_slope"], abs=3e-3)
    assert res.values["k_max"] == 0.0 and res.values["k_min"] == 0.0     # fixed parameters untouched
    assert res.final_eval.cost < 0.2


def test_degenerate_parameter_pair_is_flagged_as_correlated(base):
    """t0 and k_slope of the VTR model cannot be separated by line/space gauges (edge slope is nearly pattern-independent):
    with measurement noise the fit is non-unique and the report must say so instead of presenting the numbers as results."""
    proc, prob = base
    thr0 = proc.resist.threshold
    vproc = replace(proc, resist=VariableThreshold(t0=thr0))
    vprob = prob.derive(resist=VariableThreshold(t0=thr0))
    gauges = synthesize_measurements(vprob, {"t0": thr0 + 0.02, "k_slope": 0.04}, noise_nm=1.0, seed=5)
    params = ParameterSet.from_process(vproc, fixed=["k_max", "k_min"], start={"t0": thr0 - 0.03},
                                       bounds={"k_slope": (-0.2, 0.2)})
    p = vprob.derive(gauges, params=params)
    res = calibrate(p, OptimizeOptions(method="ls"))
    assert res.final_eval.cost < 2.0                                      # it fits the noisy data well ...
    assert any({a, b} == {"t0", "k_slope"} and abs(r) > 0.9 for a, b, r in res.correlated)     # ... but is flagged
    assert res.param_std["k_slope"] > 0.2 * abs(res.values["k_slope"])                         # and k_slope is poorly determined
    text = summary_text(p, res)
    assert "strongly correlated" in text and "t0 ~ k_slope" in text and "poorly determined" in text


def test_diffusion_blur_model_is_recovered_from_noisy_gauges(base):
    """threshold + resist blur length (compact model with one Gaussian-blurred term) are separable by dense / isolated / line-end
    gauges: they are recovered within their own +-1sigma although highly correlated, and the report does not cry wolf."""
    proc, prob = base
    thr0 = proc.resist.threshold

    def blur(thr, s):
        return CompactResist([Term(1.0, 0, 0.0, 1, 0, s, ("sigma",))], thr)
    bprob = prob.derive(resist=blur(thr0, 35.0))
    truth = {"threshold": thr0 + 0.02, "t0.sigma": 35.0}
    gauges = synthesize_measurements(bprob, truth, noise_nm=1.0, seed=11)
    params = ParameterSet.from_process(replace(proc, resist=blur(thr0 - 0.03, 15.0)))
    p = bprob.derive(gauges, params=params, cost="eq8", wt_space=3.0)
    assert p.evaluate().cost > 2.5
    res = calibrate(p, OptimizeOptions(method="de+ls", de_maxiter=15, de_popsize=8))
    assert res.success
    for n in ("threshold", "t0.sigma"):
        assert abs(res.values[n] - truth[n]) < 2.5 * res.param_std[n]
    assert res.values["t0.sigma"] == pytest.approx(35.0, rel=0.15)
    assert res.final_eval.cost < 2.5
    assert "WARNING - uncertainty" not in summary_text(p, res)


def test_noisy_data_eq8_with_extra_space_weight(base):
    proc, prob = base
    thr0 = proc.resist.threshold
    truth = {"threshold": thr0 - 0.02}
    gauges = synthesize_measurements(prob, truth, noise_nm=1.0, seed=7, weights={"SPACE": 2.0})
    p = prob.derive(gauges, params=ParameterSet.from_process(proc, start={"threshold": thr0 + 0.04}), cost="eq8", wt_space=5.0)
    assert p.evaluate().cost != pytest.approx(prob.derive(gauges, cost="eq7").evaluate().cost)
    res = calibrate(p, OptimizeOptions(method="de+ls", de_maxiter=12, de_popsize=8))
    assert res.values["threshold"] == pytest.approx(truth["threshold"], abs=0.01)
    assert res.final_eval.cost < 4.0                                     # ~ noise level, inflated by wt_space
    sd = res.param_std["threshold"]
    assert np.isfinite(sd) and 0.0 < sd < 0.01
    assert res.history[0][0] == "start" and res.history[-1][2] <= res.history[0][2]


def test_calibration_never_worse_than_start_and_no_free_parameters(base):
    proc, prob = base
    gauges = synthesize_measurements(prob, {"threshold": proc.resist.threshold + 0.02})
    p = prob.derive(gauges, params=ParameterSet.from_process(proc, fixed=["threshold"]))
    res = calibrate(p)
    assert res.free_names == [] and res.final_eval.cost == pytest.approx(res.start_eval.cost)


# ---------------------------------------------------------------- optical parameter plumbing
def test_optical_parameter_is_evaluated_and_changes_the_cost(base):
    proc, prob = base
    sub = GaugeSet([x for x in prob.gauges if x.pattern in ("iso_1500", "lineend")])
    params = ParameterSet.from_process(proc, optics=("na",), fixed=["threshold"])
    assert params.names == ["threshold", "na"] and params.free_names == ["na"]
    po = prob.derive(sub, params=params)
    truth = synthesize_measurements(po, {"na": 0.70})
    p2 = po.derive(truth)
    names = p2.params.names
    v_true = np.array([proc.resist.threshold, 0.70])
    v_off = np.array([proc.resist.threshold, 0.75])
    assert names == ["threshold", "na"]
    assert p2.evaluate(v_true).cost < 1e-6
    assert p2.evaluate(v_off).cost > 1.0
    cp = p2.calibrated_process({"na": 0.70})
    assert cp.imager.na == 0.70 and proc.imager.na == 0.75                # original untouched


# ---------------------------------------------------------------- reports
def test_report_files_and_calibrated_process(base, tmp_path):
    proc, prob = base
    thr0 = proc.resist.threshold
    gauges = synthesize_measurements(prob, {"threshold": thr0 + 0.02}, noise_nm=0.5, seed=3)
    p = prob.derive(gauges, params=ParameterSet.from_process(proc, start={"threshold": thr0 - 0.02}))
    res = calibrate(p, OptimizeOptions(method="ls"))
    paths = write_report(p, res, str(tmp_path), "demo")
    for k in ("summary", "residuals", "plot"):
        assert os.path.getsize(paths[k]) > 200
    text = summary_text(p, res)
    assert "threshold" in text and "Eq.7" in text and "CD" in text
    with open(paths["residuals"], encoding="utf-8") as fh:
        assert len(fh.read().strip().splitlines()) == len(gauges) + 1
    cp = p.calibrated_process(res.values)
    assert cp.resist.threshold == pytest.approx(res.values["threshold"])
    assert cp.resist is not p.resist                                      # independent copy
    assert cp.tone == proc.tone and cp.pixel == proc.pixel


def test_patterns_from_library_uses_cell_names():
    lib = Library()
    lib.new_cell("A").add(g.rectangle(0, 0, 100, 200))
    lib.new_cell("B").add(g.rectangle(0, 0, 50, 50), g.rectangle(100, 0, 150, 50))
    pats = patterns_from_library(lib)
    assert sorted(pats) == ["A", "B"] and len(pats["A"]) == 1 and len(pats["B"]) == 2
    assert list(patterns_from_library(lib, cells=["B"])) == ["B"]
