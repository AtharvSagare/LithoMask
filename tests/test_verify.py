"""Phase 7 tests: MRC locations, hotspot logic on synthetic prints, process-window maths, end-to-end verify + report."""
import os

import numpy as np
import pytest

from lithomask import geometry as g
from lithomask.optics import Imager, Source
from lithomask.opc import ModelOptions, OPCOptions, ProcessModel, correct
from lithomask.verify import (HotspotRules, MRCRules, VerifyOptions, WindowOptions, edge_samples, find_hotspots, run_mrc,
                            summarize_hotspots, summarize_mrc, verify, write_report)
from lithomask.verify.epe import CDMap
from lithomask.verify.window import _dof, _edges


# ---------------------------------------------------------------- MRC
def test_mrc_width_space_area_angle_grid_jog():
    clean = [g.rectangle(0, 0, 200, 1000), g.rectangle(400, 0, 600, 1000)]
    assert run_mrc(clean, MRCRules(min_width=100, min_space=100, min_area=1000, min_jog=20, grid=1)) == []
    thin = run_mrc([g.rectangle(0, 0, 60, 1000)], MRCRules(min_width=100))
    assert summarize_mrc(thin) == {"width": 1} and abs(thin[0].x - 30) < 31
    close = run_mrc([g.rectangle(0, 0, 200, 1000), g.rectangle(240, 0, 440, 1000)], MRCRules(min_space=100))
    assert summarize_mrc(close) == {"space": 1} and 200 < close[0].x < 240
    small = run_mrc([g.rectangle(0, 0, 30, 30)], MRCRules(min_area=2000))
    assert summarize_mrc(small) == {"area": 1}
    tri = run_mrc([g.polygon([(0, 0), (1000, 0), (1000, 100)])], MRCRules(min_angle_deg=80))
    assert [v.rule for v in tri] == ["angle"] and tri[0].value < 10
    off = run_mrc([g.rectangle(0.3, 0, 200, 100)], MRCRules(grid=1.0))
    assert summarize_mrc(off) == {"grid": 1}
    jog = g.polygon([(0, 0), (400, 0), (400, 200), (210, 200), (210, 210), (0, 210)])   # 10 nm step between two opposite corners
    assert "jog" in summarize_mrc(run_mrc([jog], MRCRules(min_jog=40, min_angle_deg=0)))


# ---------------------------------------------------------------- edge samples
def test_edge_samples_normals_outward_and_lineend():
    r = g.rectangle(0, 0, 100, 1000)
    S = edge_samples([r], step=100.0, lineend_max=220.0, corner_exclude=20.0)
    assert S.lineend.sum() == 2 and np.allclose(np.abs(S.normals[S.lineend][:, 1]), 1.0)
    c = np.array([50.0, 500.0])
    assert np.all(np.sum((S.points - c) * S.normals, axis=1) > 0)           # normals point away from the centre


# ---------------------------------------------------------------- hotspots (hand-made prints)
def _cdm(tcd, pcd):
    n = len(tcd)
    return CDMap(np.arange(n) * 500.0, np.zeros(n), np.ones(n), np.zeros(n), np.array(tcd, float), np.array(pcd, float))


def _kinds(target, printed, cdm=None, samples=None, epe=None):
    S = samples or edge_samples([], 20.0)
    return summarize_hotspots(find_hotspots(target, printed, S, epe if epe is not None else np.zeros(len(S)),
                                            cdm or _cdm([], []), HotspotRules()))


def test_hotspot_bridge_break_missing_extra_pinch():
    a, b = g.rectangle(0, 0, 200, 1000), g.rectangle(300, 0, 500, 1000)
    assert _kinds([a, b], [g.rectangle(0, 0, 500, 1000)]) == {"bridge": 1}
    assert _kinds([a, b], [a, b]) == {}
    assert _kinds([a], [g.rectangle(0, 0, 200, 450), g.rectangle(0, 550, 200, 1000)]) == {"break": 1}
    assert _kinds([a, b], [a]) == {"missing": 1}
    assert _kinds([a], [a, g.rectangle(1000, 0, 1100, 1000)]) == {"extra": 1}
    assert _kinds([a], [a], cdm=_cdm([200, 200, 200], [200, 150, np.nan])) == {"pinch": 2}


def test_hotspot_lineend_shortening():
    r = g.rectangle(0, 0, 100, 1000)
    S = edge_samples([r], 100.0)
    epe = np.where(S.lineend, -40.0, 0.0)
    k = _kinds([r], [r], samples=S, epe=epe)
    assert k == {"lineend": 2}


# ---------------------------------------------------------------- process-window maths
def test_window_edges_and_dof():
    doses = np.linspace(0.9, 1.1, 5)
    dev = np.array([30.0, 4.0, 0.0, 4.0, 30.0])                    # passes (tol 10) at 0.95..1.05, edges interpolated
    lo, hi = _edges(dev, doses, 10.0)
    assert lo == pytest.approx(0.95 - 0.05 * (10 - 4) / (30 - 4)) and hi == pytest.approx(1.05 + 0.05 * (10 - 4) / (30 - 4))
    assert np.isnan(_edges(np.full(5, 50.0), doses, 10.0)[0])
    f = np.array([-200.0, -100.0, 0.0, 100.0, 200.0])
    el = np.array([0.0, 8.0, 10.0, 8.0, 0.0])
    dof, bf = _dof(f, el, 4.0)
    assert dof == pytest.approx(2 * (100 + 100 * 4 / 8)) and bf == pytest.approx(0.0)
    assert _dof(f, np.zeros(5), 4.0)[0] == 0.0


# ---------------------------------------------------------------- end to end
@pytest.fixture(scope="module")
def pm():
    im = Imager(wavelength=193.0, na=0.75, source=Source.conventional(0.6))
    return ProcessModel.anchored(im, cd=180.0, tone="clear_field", shape="line", pixel=8.0)


def _lshape(w=180.0, L=800.0):
    return g.polygon([(0, 0), (L, 0), (L, w), (w, w), (w, L), (0, L)])


@pytest.fixture(scope="module")
def lshape_results(pm):
    tgt = [_lshape()]
    raw = verify(tgt, tgt, pm, VerifyOptions(window=WindowOptions(doses=(0.9, 1.0, 1.1), defoci=(-200.0, 0.0, 200.0), n_gauges=2)),
                 name="L-shape uncorrected")
    res = correct(tgt, pm, OPCOptions(stages=("model",), model=ModelOptions(max_iter=8)))
    fix = verify(tgt, res.mask, pm, VerifyOptions(mrc=MRCRules(min_width=60, min_space=60, grid=1.0)), name="L-shape OPC")
    return raw, fix


def test_verify_opc_reduces_epe_and_cd_error(lshape_results):
    raw, fix = lshape_results
    assert fix.epe_stats["rms"] < 0.5 * raw.epe_stats["rms"]
    assert fix.cd_stats["rms"] <= raw.cd_stats["rms"]
    assert raw.epe_stats["rms"] > 5.0 and len(raw.samples) == len(raw.epe)


def test_verify_window_shapes(lshape_results):
    w = lshape_results[0].window
    assert w.cd.shape == (len(w.gauges), 3, 3) and w.ok.shape == (3, 3) and w.el_pct.shape == (3,)
    assert w.max_el_pct >= 0.0 and w.dof_nm >= 0.0


def test_report_files(lshape_results, tmp_path):
    for r in lshape_results:
        p = write_report(r, str(tmp_path))
        assert os.path.getsize(p["overlay"]) > 5000
        txt = open(p["html"], encoding="utf-8").read()
        assert "EPE RMS" in txt and "data:image/png;base64" in txt
    assert "window" in write_report(lshape_results[0], str(tmp_path))
