"""Phase 5 (stages A, B) tests: fragmentation, rule-based OPC, model-based OPC (EPE must go down)."""
import numpy as np
import pytest
from shapely.ops import unary_union

from lithomask import geometry as g
from lithomask.optics import Imager, Source
from lithomask.opc import (FragmentOptions, MRCOptions, ModelOptions, OPCOptions, ProcessModel, RuleOptions, correct,
                         fragment_polygons, mrc_check, rule_opc)


def _area(polys):
    return unary_union([p.to_shapely() for p in polys]).area


def _lshape(w=180.0, L=800.0):
    return g.polygon([(0, 0), (L, 0), (L, w), (w, w), (w, L), (0, L)])


def _ro(**kw):
    base = dict(bias_table=[(100.0, 0.0), (1000.0, 0.0)])
    base.update(kw)
    return OPCOptions(rule=RuleOptions(**base))


@pytest.fixture(scope="module")
def imager():
    return Imager(wavelength=193.0, na=0.75, source=Source.conventional(0.6))


@pytest.fixture(scope="module")
def pm_line(imager):
    return ProcessModel.anchored(imager, cd=180.0, tone="clear_field", shape="line", pixel=8.0)


# ---------------------------------------------------------------- fragmentation / rebuild
def test_fragment_rebuild_zero_and_uniform_shift_exact():
    r = g.rectangle(0, 0, 400, 1000)
    fl = fragment_polygons([r], FragmentOptions())
    assert _area(fl.build(np.zeros(len(fl.frags)))) == pytest.approx(r.area)
    grown = fl.build(np.full(len(fl.frags), 10.0))
    assert _area(grown) == pytest.approx((400 + 20) * (1000 + 20))


def test_fragments_lengths_and_lineend_flag():
    fl = fragment_polygons([g.rectangle(0, 0, 100, 1000)], FragmentOptions(max_len=120, corner_len=40, min_len=40))
    ends = [e for e in fl.edges if e.lineend]
    assert len(ends) == 2 and all(abs(e.length - 100) < 1e-9 for e in ends)
    for ei, e in enumerate(fl.edges):
        if e.lineend:
            continue
        lens = [fl.frags[k].t1 - fl.frags[k].t0 for k in fl.edge_frags[ei]]
        assert sum(lens) == pytest.approx(e.length)
        assert max(lens) <= 120 + 1e-9 and min(lens) >= 40 - 1e-9


def test_lshape_uniform_shift_matches_mitre_offset_and_concave_corner():
    L = _lshape()
    fl = fragment_polygons([L], FragmentOptions())
    for d in (8.0, -8.0):
        mask = fl.build(np.full(len(fl.frags), d))
        ref = g.offset([L], d, join="miter")
        assert _area(mask) == pytest.approx(_area(ref), rel=1e-9)
    assert sum(1 for e in fl.edges if not e.convex_a) == 1          # exactly one concave vertex


def test_jog_between_fragments_is_manhattan():
    fl = fragment_polygons([g.rectangle(0, 0, 400, 1000)], FragmentOptions())
    d = np.zeros(len(fl.frags))
    d[fl.edge_frags[0][1]] = 12.0                                   # shift one fragment of the first edge
    P = fl.build(d)[0].outer
    seg = np.roll(P, -1, axis=0) - P
    assert np.all((np.abs(seg[:, 0]) < 1e-9) | (np.abs(seg[:, 1]) < 1e-9))


# ---------------------------------------------------------------- rule based
def test_rule_bias_isolated_vs_dense():
    opt = _ro(bias_table=[(100.0, -6.0), (400.0, 0.0), (1000.0, 10.0)])
    iso = rule_opc([g.rectangle(0, 0, 200, 2000)], opt)
    assert iso[0].bbox[2] - iso[0].bbox[0] == pytest.approx(220.0)    # isolated: +10 each side
    dense = rule_opc([g.rectangle(0, 0, 200, 2000), g.rectangle(300, 0, 500, 2000)], opt)
    left = min(dense, key=lambda p: p.bbox[0])
    assert left.bbox[2] == pytest.approx(200.0 - 6.0)                 # facing edge (100 nm gap) pulled in
    assert left.bbox[0] < 0.0                                          # outer edge grows


def test_rule_serif_notch_hammer_contact():
    sq = g.rectangle(0, 0, 400, 400)
    assert _area(rule_opc([sq], _ro(serif=40.0))) == pytest.approx(400 * 400 + 4 * 0.75 * 40 * 40)
    L = _lshape()
    assert _area(rule_opc([L], _ro(notch=20.0))) == pytest.approx(L.area - 0.75 * 20 * 20)
    line = g.rectangle(0, 0, 100, 1000)
    out = rule_opc([line], _ro(hammer_ext=10.0, hammer_wing=15.0, hammer_len=30.0))
    assert _area(out) == pytest.approx(100 * 1020 + 2 * (2 * 15 * 30))   # 2 line ends, 2 wings each
    out = rule_opc([g.rectangle(0, 0, 200, 200)], _ro(contact_bias=5.0))
    assert _area(out) == pytest.approx(210 * 210)


# ---------------------------------------------------------------- model based: EPE must go down
def _check_reduction(target, pm, factor=0.75):
    opts = OPCOptions(stages=("model",), model=ModelOptions(max_iter=8, gain=0.6, tol=1.0),
                      mrc=MRCOptions(grid=1.0, max_bias=60.0))
    res = correct(target, pm, opts)
    assert res.stage_used == "model"
    assert res.epe_after["rms"] <= res.epe_before["rms"] * factor, (res.epe_before, res.epe_after)
    rms = [r["rms"] for r in res.log]
    assert rms[0] == pytest.approx(res.epe_before["rms"]) and min(rms) == pytest.approx(res.epe_after["rms"])
    assert res.library.cells["OPC_TOP"].polygons == res.polygons
    return res


def test_model_opc_lshape_reduces_epe(pm_line):
    _check_reduction([_lshape()], pm_line)


def test_model_opc_line_end_reduces_pullback(pm_line):
    res = _check_reduction([g.rectangle(0, 0, 180, 1200)], pm_line)
    assert max(p.bbox[3] - p.bbox[1] for p in res.polygons) > 1200.0   # line-end pullback is compensated


def test_model_opc_contact_array_reduces_epe(imager):
    pm = ProcessModel.anchored(imager, cd=200.0, tone="dark_field", shape="hole", pixel=8.0)
    holes = [g.rectangle(x, y, x + 200, y + 200) for x in (0, 400, 800) for y in (0, 400, 800)]
    res = _check_reduction(holes, pm, factor=0.9)
    assert len(res.polygons) == 9


def test_cancel_via_progress_and_validation(pm_line):
    calls = []
    opts = OPCOptions(stages=("model",), model=ModelOptions(max_iter=8),
                      progress=lambda s, it, rms, mx: calls.append(it) or (it < 1))
    res = correct([_lshape()], pm_line, opts)
    assert calls == [0, 1] and len(res.log) == 2
    with pytest.raises(ValueError):
        correct([_lshape()], pm_line, OPCOptions(stages=("rule", "model")))
    with pytest.raises(ValueError):
        correct([], pm_line)


def test_rule_stage_through_correct_reports(pm_line):
    res = correct([_lshape()], pm_line, OPCOptions(stages=("rule",), rule=RuleOptions(
        bias_table=[(100.0, 0.0), (1000.0, 8.0)], serif=30.0)))
    assert res.stage_used == "rule" and res.polygons and "rms" in res.epe_after
    assert mrc_check(res.polygons)["ok"]
