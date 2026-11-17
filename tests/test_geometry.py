import math
import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lithomask import (Polygon, rectangle, polygon, circle, ellipse, regular_polygon, ring, path,
                     boolean, union, offset, total_area)


def test_rectangle_basics_and_orientation():
    r = rectangle(10, 20, 110, 70)
    assert r.area == pytest.approx(5000.0)
    assert r.bbox == (10, 20, 110, 70)
    assert r.outer.shape == (4, 2)
    # CCW => positive signed area
    x, y = r.outer[:, 0], r.outer[:, 1]
    assert 0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0


def test_clockwise_input_is_normalised_and_closing_vertex_dropped():
    p = polygon([(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)])
    assert len(p.outer) == 4
    assert p.area == pytest.approx(100.0)


def test_circle_area_and_sagitta():
    r, tol = 5000.0, 0.1
    c = circle(0, 0, r, tol=tol)
    assert c.area == pytest.approx(math.pi * r * r, rel=2e-3)
    assert np.allclose(np.hypot(c.outer[:, 0], c.outer[:, 1]), r)  # vertices on the circle
    n = len(c.outer)
    assert r * (1 - math.cos(math.pi / n)) <= tol + 1e-9


def test_ellipse_area():
    e = ellipse(0, 0, 3000, 1000, tol=0.2)
    assert e.area == pytest.approx(math.pi * 3000 * 1000, rel=2e-3)


def test_ring_area_has_hole():
    rg = ring(0, 0, 1000, 2000, tol=0.1)
    assert len(rg.holes) == 1
    assert rg.area == pytest.approx(math.pi * (2000**2 - 1000**2), rel=2e-3)
    assert rg.is_valid()


def test_regular_polygon_area():
    n, r = 6, 100.0
    assert regular_polygon(0, 0, r, n).area == pytest.approx(0.5 * n * r * r * math.sin(2 * math.pi / n))


def test_boolean_area_identities():
    a = [rectangle(0, 0, 100, 100)]
    b = [rectangle(50, 50, 150, 150)]
    A, B = 10000.0, 10000.0
    I = total_area(boolean(a, b, "and"))
    assert I == pytest.approx(2500.0)
    assert total_area(boolean(a, b, "or")) == pytest.approx(A + B - I)
    assert total_area(boolean(a, b, "xor")) == pytest.approx(A + B - 2 * I)
    assert total_area(boolean(a, b, "not")) == pytest.approx(A - I)


def test_union_merges_overlaps():
    u = union([rectangle(0, 0, 100, 100), rectangle(50, 0, 150, 100)])
    assert len(u) == 1 and u[0].area == pytest.approx(15000.0)


def test_offset_rectangle_miter_and_shrink():
    r = [rectangle(0, 0, 200, 100)]
    g = offset(r, 10, join="miter")
    assert total_area(g) == pytest.approx(220 * 120)
    s = offset(r, -10, join="miter")
    assert total_area(s) == pytest.approx(180 * 80)
    assert offset(r, -60) == []  # collapses


def test_offset_round_area():
    w, h, d = 200.0, 100.0, 10.0
    g = offset([rectangle(0, 0, w, h)], d, join="round", tol=0.01)
    expect = w * h + 2 * d * (w + h) + math.pi * d * d
    assert total_area(g) == pytest.approx(expect, rel=1e-3)


def test_transforms():
    r = rectangle(0, 0, 100, 50)
    assert r.rotate(90).bbox == pytest.approx((-50, 0, 0, 100))
    assert r.mirror("x").bbox == pytest.approx((0, -50, 100, 0))
    assert r.mirror("y").bbox == pytest.approx((-100, 0, 0, 50))
    assert r.scale(2).area == pytest.approx(4 * r.area)
    assert r.translate(5, -5).bbox == pytest.approx((5, -5, 105, 45))
    assert r.rotate(45, center=(50, 25)).area == pytest.approx(r.area)


def test_snap():
    p = polygon([(0.2, 0.2), (10.4, 0.1), (10.2, 9.9), (0.3, 10.2)]).snap(1.0)
    assert np.all(p.outer == np.round(p.outer))


def test_check_flags_bowtie_and_degenerate():
    bow = polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert any("invalid" in s for s in bow.check())
    assert polygon([(0, 0), (1, 0), (2, 0)]).check()  # zero area
    assert rectangle(0, 0, 5, 5).check() == []


def test_path_areas():
    pts = [(0, 0), (1000, 0)]
    assert sum(p.area for p in path(pts, 100, end="flat")) == pytest.approx(1000 * 100)
    assert sum(p.area for p in path(pts, 100, end="square")) == pytest.approx(1100 * 100)
    rnd = sum(p.area for p in path(pts, 100, end="round", tol=0.01))
    assert rnd == pytest.approx(1000 * 100 + math.pi * 50**2, rel=1e-3)


def test_path_l_shape_miter_corner():
    p = path([(0, 0), (1000, 0), (1000, 1000)], 100, join="miter")
    # horizontal strip 1000x100 + vertical part above y=50 (100 x 950) + mitred corner block (50 x 100) = 200000
    assert len(p) == 1 and p[0].is_valid()
    assert p[0].area == pytest.approx(200000.0)
