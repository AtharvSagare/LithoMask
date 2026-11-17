"""Phase 4 tests: resist models, contour extraction, CD / EP measurement, end-to-end print simulation."""
import math

import numpy as np
import pytest
from scipy import ndimage

from lithomask import geometry as g
from lithomask.optics import Grid, Imager, Source
from lithomask.resist import (CompactResist, ConstantThreshold, Term, VariableThreshold, crossings, derivative,
                            edge_placement, extract_contours, gauss_laguerre_kernel, measure_cd, simulate_print)


# ---------------------------------------------------------------- contours
def test_contour_of_circular_blob_radius_error_below_tenth_pixel():
    pix, R0 = 5.0, 203.3
    grid = Grid(-400, -400, 160, 160, pix)
    X, Y = np.meshgrid(grid.xs, grid.ys)
    F = np.tanh((R0 - np.hypot(X, Y)) / (2 * pix))           # smooth blob, level 0 is the exact circle
    polys = extract_contours(F, grid, 0.0, "bright")
    assert len(polys) == 1 and not polys[0].holes
    r = np.hypot(polys[0].outer[:, 0], polys[0].outer[:, 1])
    assert np.max(np.abs(r - R0)) < 0.1 * pix
    assert polys[0].area == pytest.approx(math.pi * R0 ** 2, rel=2e-3)


def test_contour_holes_dark_region_and_border_clipping():
    grid = Grid(0, 0, 100, 100, 10.0)
    X, Y = np.meshgrid(grid.xs, grid.ys)
    r = np.hypot(X - 500, Y - 500)
    F = np.where((r > 150) & (r < 350), 1.0, 0.0)
    ring = extract_contours(F, grid, 0.5, "bright")
    assert len(ring) == 1 and len(ring[0].holes) == 1
    assert ring[0].area == pytest.approx(math.pi * (350 ** 2 - 150 ** 2), rel=0.05)
    dark = extract_contours(F, grid, 0.5, "dark")                # outside + inner disc; outside touches the border
    assert len(dark) == 2
    assert sum(p.area for p in dark) == pytest.approx(1000 * 1000 - ring[0].area, rel=1e-6)


# ---------------------------------------------------------------- measurements
def test_crossings_cd_and_edge_placement_on_ramps():
    grid = Grid(0, 0, 100, 20, 10.0)
    X = grid.xs[None, :] * np.ones((20, 1))
    F = np.abs(X - 500.0)                                        # V shaped: dark (low) between 500 +- level
    for lv in (40.0, 123.0):
        assert measure_cd(F, grid, (200, 100), (800, 100), lv, "dark") == pytest.approx(2 * lv, abs=0.5)
    assert math.isnan(measure_cd(F, grid, (200, 100), (800, 100), 40.0, "bright"))
    # signed placement along +x from x=520: crossing of level 40 on the right edge is at 540 -> +20
    assert edge_placement(F, grid, (520, 100), (1, 0), 40.0, 100.0) == pytest.approx(20.0, abs=0.6)
    assert edge_placement(F, grid, (520, 100), (-1, 0), 40.0, 100.0) == pytest.approx(-20.0, abs=0.6)
    s = np.array([0.0, 1.0, 2.0])
    assert crossings(s, np.array([0.0, 1.0, 0.0]), 0.5) == pytest.approx([0.5, 1.5])


# ---------------------------------------------------------------- resist models
def test_threshold_model_cd_vs_coherent_analytic_oracle():
    # coherent grating (sigma->0) has an exact Fourier-series image; bright-line width at threshold must match.
    lam, na, P, pix = 193.0, 0.75, 400.0, 5.0
    npx = int(P / pix)
    row = np.tile(np.r_[np.ones(npx // 2), np.zeros(npx // 2)], 4)
    t = np.tile(row, (len(row), 1))
    n = len(row)
    im = Imager(lam, na, Source.conventional(1e-4), kernel_nm=n * pix, energy=1.0, max_kernels=5000)
    I = im.image_from_transmission(t, pix, "periodic")
    grid = Grid(0, 0, n, n, pix)
    xf = np.linspace(0, P, 40001)
    A = sum(0.5 * np.sinc(k * 0.5) * np.exp(-1j * np.pi * k * 0.5) * np.sinc(k * pix / P) * np.exp(2j * np.pi * k * xf / P)
            for k in range(-int(na * P / lam), int(na * P / lam) + 1))
    Iana = np.abs(A) ** 2
    for thr in (0.3, 0.5):
        above = xf[Iana > thr]
        cd_ana = above.max() - above.min()
        cd = measure_cd(I, grid, (300, 800), (700, 800), thr, "bright")      # bright line centred at x = 500
        assert cd == pytest.approx(cd_ana, abs=1.5)
    # model API: constant threshold yields (I, level); higher threshold narrows a bright line
    F, lv = ConstantThreshold(0.4).field(I, pix)
    assert lv == 0.4 and F is I
    assert measure_cd(I, grid, (300, 800), (700, 800), 0.5) < measure_cd(I, grid, (300, 800), (700, 800), 0.3)


def test_variable_threshold_reduces_to_constant_and_responds_to_imax():
    rng = np.random.default_rng(0)
    I = ndimage.gaussian_filter(rng.random((60, 60)), 3)
    vt = VariableThreshold(t0=0.4)
    F, lv = vt.field(I, 10.0)
    assert lv == 0.0 and np.allclose(F, I - 0.4)
    vt2 = VariableThreshold(t0=0.2, k_max=0.5)
    T = vt2.threshold_map(I, 10.0)
    assert np.all(T >= 0.2 + 0.5 * I - 1e-12)                    # local max >= I
    v = vt2.get_vector()
    vt2.set_vector(v * 1.0)
    assert len(vt2.bounds()) == len(v) == len(vt2.param_names())


def test_compact_terms_match_scipy_operators():
    rng = np.random.default_rng(2)
    I = ndimage.gaussian_filter(rng.random((80, 80)), 2)
    pix = 10.0
    assert np.allclose(Term(1.0).evaluate(I, pix), I)
    assert np.allclose(Term(2.0, 0, 0.1, 1, 0, 30.0).evaluate(I, pix),
                       2.0 * ndimage.gaussian_filter(I + 0.1, 3.0, mode="reflect", truncate=4.0))
    ramp = np.tile(np.arange(40) * 0.01, (40, 1))                  # dI/dx = 0.01 per pixel = 0.001 / nm
    assert np.allclose(derivative(ramp, pix, 1)[5:-5, 5:-5], 0.001)
    sq = Term(1.0, 0, 0.0, 2, 0, 30.0).evaluate(I, pix)            # sqrt(blur(I^2)) >= blur(I)  (Jensen)
    assert np.all(sq >= ndimage.gaussian_filter(I, 3.0, mode="reflect", truncate=4.0) - 1e-9)
    gl = gauss_laguerre_kernel(30.0, 10.0, order=2)
    assert gl.shape[0] == gl.shape[1] and gl.shape[0] % 2 == 1 and np.abs(gl).sum() == pytest.approx(1.0)
    assert Term(1.0, 0, 0.0, 1, 2, 30.0).evaluate(I, pix).shape == I.shape


def test_compact_model_identity_equals_threshold_and_param_vector_roundtrip():
    I = np.random.default_rng(3).random((30, 30))
    F, lv = CompactResist([Term(1.0)], 0.35).field(I, 10.0)
    assert np.allclose(F, I) and lv == 0.35
    m = CompactResist.example_8term(0.33)
    v = m.get_vector()
    names, bnds = m.param_names(), m.bounds()
    assert len(names) == len(v) == len(bnds) and names[0] == "threshold"
    assert all(lo <= x <= hi for x, (lo, hi) in zip(v, bnds))
    v2 = v.copy()
    v2[names.index("t1.sigma")] = 45.0
    m.set_vector(v2)
    assert m.terms[1].sigma == 45.0 and np.allclose(m.get_vector(), v2)
    F, _ = m.field(I, 10.0)
    assert F.shape == I.shape and np.all(np.isfinite(F))


# ---------------------------------------------------------------- end to end
def test_end_to_end_isolated_line_and_contact_hole():
    lam, na = 193.0, 0.75
    im = Imager(lam, na, Source.annular(0.5, 0.8))
    pix = 8.0
    # (a) 200 nm chrome line in clear field -> dark line prints; CD at the threshold that places the drawn edge
    grid = Grid(-800, -800, 200, 200, pix)
    line = g.rectangle(-100, -700, 100, 700)
    I = im.image([line], grid, tone="clear_field")
    thr = float(np.interp(100.0, grid.xs, I[100]))
    pr = simulate_print([line], im, ConstantThreshold(thr), grid, "clear_field")
    cd = measure_cd(pr.field, grid, (-400, 0), (400, 0), pr.level, "dark")
    assert cd == pytest.approx(200.0, abs=2.0)
    assert len(pr.contours) == 2 or len(pr.contours) == 1           # resist either side of the line (bright region split)
    # (b) contact hole opening prints as one closed polygon of area ~ drawn area
    hole = g.circle(0, 0, 100.0)
    pr2 = simulate_print([hole], im, ConstantThreshold(0.3), Grid(-800, -800, 200, 200, pix), "dark_field")
    assert len(pr2.contours) == 1 and not pr2.contours[0].holes
    assert 0.3 * math.pi * 100 ** 2 < pr2.contours[0].area < 3.0 * math.pi * 100 ** 2
    cx, cy = pr2.contours[0].outer.mean(axis=0)
    assert abs(cx) < 2 and abs(cy) < 2
