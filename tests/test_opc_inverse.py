"""Phase 5 (stages C, D) tests: inverse-OPC gradient vs finite differences, EPE reduction, SRAF placement / printability."""
import numpy as np
import pytest

from lithomask import geometry as g
from lithomask.optics import Grid, Imager, Source
from lithomask.opc import (InverseOptions, InverseProblem, ModelOptions, MRCOptions, OPCOptions, ProcessModel, SRAFOptions,
                         correct, place_srafs, verify_srafs)
from lithomask.opc.inverse import tv_cost_grad


@pytest.fixture(scope="module")
def imager():
    return Imager(wavelength=193.0, na=0.75, source=Source.conventional(0.6))


@pytest.fixture(scope="module")
def pm_line(imager):
    return ProcessModel.anchored(imager, cd=180.0, tone="clear_field", shape="line", pixel=8.0)


# ---------------------------------------------------------------- gradient
@pytest.mark.parametrize("tone", ["clear_field", "dark_field"])
def test_inverse_gradient_matches_finite_differences(imager, tone):
    pm = ProcessModel.anchored(imager, cd=200.0, tone=tone, shape="hole" if tone == "dark_field" else "line", pixel=20.0)
    grid = Grid(-400.0, -400.0, 24, 24, 20.0)
    target = [g.rectangle(-100, -100, 100, 100)]
    extra = [g.rectangle(250, -100, 290, 100)]
    prob = InverseProblem(target, pm, InverseOptions(pixel=20.0, steepness=25.0), grid, extra)
    rng = np.random.default_rng(1)
    theta = rng.uniform(0.3, 2.8, grid.shape)
    gamma, tv = 0.05, 0.03
    J, grad, _, _ = prob.cost_grad(theta, gamma, tv)
    for _ in range(8):
        iy, ix = rng.integers(0, 24, 2)
        h = 1e-5
        tp, tm = theta.copy(), theta.copy()
        tp[iy, ix] += h
        tm[iy, ix] -= h
        fd = (prob.cost_grad(tp, gamma, tv)[0] - prob.cost_grad(tm, gamma, tv)[0]) / (2 * h)
        assert grad[iy, ix] == pytest.approx(fd, rel=1e-4, abs=1e-7)


def test_tv_gradient_matches_finite_differences():
    rng = np.random.default_rng(3)
    m = rng.uniform(0, 1, (9, 11))
    J, G = tv_cost_grad(m)
    for _ in range(6):
        iy, ix = rng.integers(0, 9), rng.integers(0, 11)
        mp, mm = m.copy(), m.copy()
        mp[iy, ix] += 1e-6
        mm[iy, ix] -= 1e-6
        fd = (tv_cost_grad(mp)[0] - tv_cost_grad(mm)[0]) / 2e-6
        assert G[iy, ix] == pytest.approx(fd, rel=1e-5, abs=1e-8)


# ---------------------------------------------------------------- inverse OPC end to end
def test_inverse_opc_reduces_epe_line_end(pm_line):
    opts = OPCOptions(stages=("inverse",), inverse=InverseOptions(pixel=10.0, iterations=40, step=0.4, min_feature=40.0))
    res = correct([g.rectangle(0, 0, 180, 1200)], pm_line, opts)
    assert res.stage_used == "inverse" and res.polygons
    assert res.epe_after["rms"] <= 0.8 * res.epe_before["rms"], (res.epe_before, res.epe_after)
    costs = [r["cost"] for r in res.log]
    assert costs[-1] < costs[0]
    assert max(p.bbox[3] - p.bbox[1] for p in res.polygons) > 1200.0     # extended past the drawn end


def test_inverse_manhattan_output_is_rectilinear(pm_line):
    opts = OPCOptions(stages=("inverse",), inverse=InverseOptions(pixel=10.0, iterations=10, manhattan=True))
    res = correct([g.rectangle(0, 0, 180, 1200)], pm_line, opts)
    for p in res.mask:
        seg = np.roll(p.outer, -1, axis=0) - p.outer
        assert np.all((np.abs(seg[:, 0]) < 1e-6) | (np.abs(seg[:, 1]) < 1e-6))


def test_inverse_rejects_non_threshold_resist(imager):
    from lithomask.resist import VariableThreshold
    pm = ProcessModel(imager, VariableThreshold(), "clear_field")
    with pytest.raises(ValueError):
        correct([g.rectangle(0, 0, 180, 600)], pm, OPCOptions(stages=("inverse",)))


# ---------------------------------------------------------------- SRAF
def test_sraf_placement_isolated_vs_dense():
    iso = [g.rectangle(0, 0, 180, 2000)]
    bars = place_srafs(iso, OPCOptions())
    assert len(bars) >= 4                                              # both long sides, up to 2 bars each
    sr = SRAFOptions()
    left = [b for b in bars if b.p0[0] < 0 and abs(b.p0[0] - b.p1[0]) < 1e-9]
    assert sorted(round(-b.p0[0] - sr.width / 2) for b in left) == [150, 290]      # distance 150, pitch 140
    dense = [g.rectangle(0, 0, 180, 2000), g.rectangle(300, 0, 480, 2000)]       # 120 nm gap: no room between
    gap_bars = [b for b in place_srafs(dense, OPCOptions()) if 180 < b.p0[0] < 300]
    assert gap_bars == []


def test_sraf_do_not_print_and_unprintable_are_removed(pm_line):
    opts = OPCOptions()
    main = [g.rectangle(0, 0, 180, 2000)]
    bars = place_srafs(main, opts)
    ok = verify_srafs(bars, main, pm_line, opts)
    assert ok and len(ok) <= len(bars)
    # force printing: very wide bars must be narrowed or dropped, never kept wide
    fat = place_srafs(main, OPCOptions(sraf=SRAFOptions(width=120.0, distance=150.0)))
    kept = verify_srafs(fat, main, pm_line, opts)
    assert all(b.width < 120.0 for b in kept)


def test_sraf_plus_model_opc_pipeline(pm_line):
    opts = OPCOptions(stages=("sraf", "model"), model=ModelOptions(max_iter=6), mrc=MRCOptions(max_bias=60.0))
    res = correct([g.rectangle(0, 0, 180, 1400)], pm_line, opts)
    assert res.srafs and res.stage_used == "model"
    assert res.epe_after["rms"] < res.epe_before["rms"]
    assert len(res.library.cells["OPC_TOP"].polygons) == len(res.polygons) + len(res.srafs)
