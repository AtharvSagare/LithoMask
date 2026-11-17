"""Phase 3 tests: rasterisation, source/pupil, SOCS vs Abbe, coherent analytic limit, cutoff pitch, clear field."""
import math

import numpy as np
import pytest

from lithomask import geometry as g
from lithomask.optics import Grid, Imager, Source, pupil, rasterize_coverage, transmission_from_polygons


# ---------------------------------------------------------------- rasterisation
def test_coverage_area_and_antialiasing():
    grid = Grid(0, 0, 40, 40, 10.0)
    r = g.rectangle(35, 52, 213, 177)                       # edges off the pixel grid
    cov = rasterize_coverage([r], grid, supersample=10)
    assert cov.sum() * 100 == pytest.approx(r.area, rel=2e-3)
    assert cov.max() <= 1.0 and 0 < cov[5, 3] < 1           # partial pixel on the edge


def test_coverage_circle_holes_and_union():
    grid = Grid(-500, -500, 100, 100, 10.0)
    ring = g.ring(0, 0, 150, 300, tol=0.5)
    cov = rasterize_coverage([ring], grid, supersample=6)
    assert cov.sum() * 100 == pytest.approx(math.pi * (300 ** 2 - 150 ** 2), rel=3e-3)
    assert cov[50, 50] == 0                                  # hole centre is empty
    a, b = g.rectangle(0, 0, 100, 100), g.rectangle(50, 0, 150, 100)
    c2 = rasterize_coverage([a, b], Grid(0, 0, 20, 12, 10.0), supersample=2)
    assert c2.max() == 1.0 and c2.sum() * 100 == pytest.approx(150 * 100)   # overlap not double counted


def test_transmission_tones():
    grid = Grid(0, 0, 10, 10, 10.0)
    r = g.rectangle(0, 0, 50, 100)
    assert transmission_from_polygons([r], grid, "clear_field")[5, 0] == 0
    assert transmission_from_polygons([r], grid, "dark_field")[5, 0] == 1
    t = transmission_from_polygons([r], grid, "clear_field", fg=-math.sqrt(0.06))
    assert t[5, 0] == pytest.approx(-math.sqrt(0.06)) and t[5, 9] == 1


# ---------------------------------------------------------------- sources / pupil
def test_sources_shapes():
    s = np.linspace(-1, 1, 201)
    X, Y = np.meshgrid(s, s)
    for src in (Source.conventional(0.6), Source.annular(0.4, 0.8), Source.dipole(0.5, 0.9), Source.quasar(0.5, 0.9)):
        J = src(X, Y)
        assert J.sum() > 0 and np.hypot(X, Y)[J > 0].max() <= src.sigma_max + 1e-6
    d = Source.dipole(0.5, 0.9)(X, Y)
    assert d[100, 190] == 1 and d[190, 100] == 0 and d[100, 100] == 0
    q = Source.quasar(0.5, 0.9)(X, Y)
    assert q[100 + 55, 100 + 55] == 1 and q[100, 190] == 0
    assert Source.conventional(0.5)(np.array([0.5]), np.array([0.0]))[0] == 1


def test_pupil_cutoff_and_phase():
    lam, na = 193.0, 0.75
    f = na / lam
    H = pupil(np.array([0.99 * f, 1.01 * f]), np.zeros(2), lam, na)
    assert H[0] == 1 and H[1] == 0
    Hd = pupil(np.array([0.0, 0.5 * f]), np.zeros(2), lam, na, defocus_nm=100.0)
    assert Hd[0] == pytest.approx(1) and abs(Hd[1]) == pytest.approx(1) and abs(np.angle(Hd[1])) > 0.1
    with pytest.raises(KeyError):
        pupil(np.zeros(1), np.zeros(1), lam, na, aberrations={"bogus": 1.0})


# ---------------------------------------------------------------- imaging
def _grating(pitch_nm, pixel, n_periods, duty=0.5):
    npx = int(round(pitch_nm / pixel))
    one = np.zeros(npx)
    one[: int(round(duty * npx))] = 1.0                      # clear part (dark-field style: 1 = transmitting)
    row = np.tile(one, n_periods)
    return np.tile(row, (len(row), 1)), len(row)             # square: y-uniform lines, periodic in x


@pytest.mark.parametrize("src,defocus", [(Source.conventional(0.5), 0.0), (Source.annular(0.4, 0.8), 0.0),
                                         (Source.conventional(0.6), 150.0)])
def test_clear_field_is_unity(src, defocus):
    im = Imager(193.0, 0.75, src, defocus_nm=defocus, kernel_nm=1200.0)
    I = im.image_from_transmission(np.ones((120, 120)), 10.0, "periodic")
    assert np.allclose(I, 1.0, atol=1e-3)   # SOCS truncation (99 % energy) leaves ~1e-4
    # clear field also unity for the truncated SOCS and the padded boundary
    I2 = im.image_from_transmission(np.ones((200, 200)), 10.0, "padded", 1.0)
    assert np.allclose(I2, 1.0, atol=5e-3)


def test_socs_equals_abbe_and_truncation_error():
    n = 120
    t = (np.random.default_rng(1).random((n, n)) > 0.7).astype(float)    # arbitrary 2-D binary mask
    im = Imager(193.0, 0.75, Source.annular(0.4, 0.8), kernel_nm=n * 10.0, energy=1.0, max_kernels=4000)
    ref = im.abbe_image(t, 10.0)
    full = im.image_from_transmission(t, 10.0, "periodic")
    assert np.max(np.abs(full - ref)) < 1e-9
    im99 = Imager(193.0, 0.75, Source.annular(0.4, 0.8), kernel_nm=n * 10.0, energy=0.99)
    K = im99.kernels(10.0)
    assert 0.99 <= K.energy_fraction <= 1.0 and np.all(np.diff(K.lams) <= 1e-12) and K.n < K.n_total
    err = np.max(np.abs(im99.image_from_transmission(t, 10.0, "periodic") - ref))
    assert err < 0.03


def test_coherent_limit_matches_fourier_series():
    # sigma -> 0: single on-axis source point = coherent imaging; exact periodic oracle:
    # A(x) = sum_{|n|/P <= NA/lam} c_n exp(2 pi i n x / P), c_n = duty*sinc(n*duty) (x pixel-box factor of the raster)
    lam, na, P, pix = 193.0, 0.75, 400.0, 5.0
    t, n = _grating(P, pix, 4, duty=0.5)
    t = t[:, :n]
    im = Imager(lam, na, Source.conventional(1e-4), kernel_nm=n * pix, energy=1.0, max_kernels=5000)
    I = im.image_from_transmission(t, pix, "periodic")
    x = (np.arange(n) + 0.5) * pix
    A = np.zeros(n, dtype=complex)
    for k in range(-int(na * P / lam), int(na * P / lam) + 1):
        # box on [0, P/2]: c_k = 0.5 sinc(k/2) exp(-i pi k/2); raster pixel-averaging adds sinc(k pix/P)
        c = 0.5 * np.sinc(k * 0.5) * np.exp(-1j * np.pi * k * 0.5) * np.sinc(k * pix / P)
        A += c * np.exp(2j * np.pi * k * x / P)
    ref = np.abs(A) ** 2
    assert np.max(np.abs(I[0] - ref)) < 2e-3 * ref.max()


def test_cutoff_pitch_dense_lines():
    lam, na, sig = 193.0, 0.75, 0.5
    # 190 nm (not ~175): the lattice-sampled source stays clear of the exact cutoff pitch 171.6 nm
    for pitch, resolved in ((160.0, False), (190.0, True)):
        pix = 5.0
        t, n = _grating(pitch, pix, 10, duty=0.5)
        im = Imager(lam, na, Source.conventional(sig), kernel_nm=n * pix)
        row = im.image_from_transmission(t[:, :n], pix, "periodic")[0]
        c = (row.max() - row.min()) / (row.max() + row.min())
        assert (c > 0.02) if resolved else (c < 1e-9)
    assert Imager(lam, na, Source.conventional(sig)).cutoff_pitch_dense == pytest.approx(lam / (na * 1.5))


def test_defocus_reduces_contrast_and_padded_contact():
    lam, na = 193.0, 0.75
    pix = 10.0
    grid = Grid(-1000, -1000, 200, 200, pix)
    hole = g.circle(0, 0, 150.0, tol=1.0)
    im0 = Imager(lam, na, Source.annular(0.5, 0.8))
    im1 = Imager(lam, na, Source.annular(0.5, 0.8), defocus_nm=300.0)
    I0 = im0.image([hole], grid, tone="dark_field")
    I1 = im1.image([hole], grid, tone="dark_field")
    assert I0[100, 100] == pytest.approx(I0.max(), rel=1e-6) and I0.max() < 1.0 and I0.min() >= -1e-12
    assert I1.max() < I0.max()
    assert I0[5, 5] < 1e-2 * I0.max() * 10               # dark far from the hole (padded boundary, no wrap)
