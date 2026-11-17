import math
import os
import sys

import numpy as np
import pytest
import shapely

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lithomask import Library, Cell, rectangle, circle, ring, polygon, total_area, Polygon


def _union_geom(polys):
    return shapely.union_all([p.to_shapely() for p in polys])


def _same_geometry(pa, pb, tol_nm2=1e-2):
    ga, gb = _union_geom(pa), _union_geom(pb)
    return ga.symmetric_difference(gb).area <= tol_nm2


def _hier_lib(dbu=1.0):
    lib = Library("T", dbu_nm=dbu)
    unit = lib.new_cell("UNIT")
    unit.add(rectangle(0, 0, 100, 40, layer=1), polygon([(0, 0), (30, 0), (0, 60)], layer=2, datatype=3))
    top = lib.new_cell("TOP")
    top.add(rectangle(-500, -500, -400, -300, layer=5))
    top.add_ref(unit, origin=(1000, 2000), rotation=90, columns=3, rows=2, spacing=(250, 300))
    top.add_ref(unit, origin=(-2000, 500), rotation=30, x_reflection=True, magnification=1.5)
    return lib, top


def test_gds_roundtrip_flat_exact(tmp_path):
    lib = Library("T")
    c = lib.new_cell("TOP")
    c.add(rectangle(0, 0, 1000, 500, layer=1), polygon([(0, 0), (2000, 0), (1000, 1730)], layer=7, datatype=2))
    f = str(tmp_path / "a.gds")
    lib.write_gds(f)
    back = Library.read_gds(f)
    a = sorted((p.layer, p.datatype, tuple(map(tuple, np.round(p.outer, 3)))) for p in c.polygons)
    b = sorted((p.layer, p.datatype, tuple(map(tuple, np.round(q.outer, 3)))) for q in back.flatten("TOP") for p in [q])
    # compare as sets of vertices per polygon irrespective of start vertex
    sa = sorted((l, d, frozenset(v)) for l, d, v in a)
    sb = sorted((l, d, frozenset(v)) for l, d, v in b)
    assert sa == sb


def test_gds_hierarchy_array_rotation_matches_gdstk(tmp_path):
    import gdstk

    lib, top = _hier_lib(dbu=0.001)
    f = str(tmp_path / "h.gds")
    lib.write_gds(f)
    # (1) our own re-read, flattened
    back = Library.read_gds(f)
    mine = back.flatten("TOP")
    assert _same_geometry(mine, top.flatten(), tol_nm2=1.0)
    # (2) independent flatten by gdstk itself (validates array-spacing frame + transform order)
    g = gdstk.read_gds(f)
    gtop = [c for c in g.cells if c.name == "TOP"][0]
    gp = [np.asarray(p.points) * 1e3 for p in gtop.get_polygons()]
    ref = [Polygon(pts) for pts in gp]
    assert _same_geometry(ref, top.flatten(), tol_nm2=1.0)


def test_hierarchy_preserved_on_read(tmp_path):
    lib, _ = _hier_lib()
    f = str(tmp_path / "h.gds")
    lib.write_gds(f)
    back = Library.read_gds(f)
    assert set(back.cells) == {"UNIT", "TOP"}
    arr = [r for r in back.cells["TOP"].references if r.columns > 1]
    assert len(arr) == 1 and (arr[0].columns, arr[0].rows) == (3, 2)
    assert [c.name for c in back.top_cells()] == ["TOP"]


def test_polygon_with_hole_roundtrip(tmp_path):
    lib = Library("H")
    c = lib.new_cell("TOP")
    rg = ring(0, 0, 1000, 2000, tol=0.5, layer=4)
    c.add(rg)
    f = str(tmp_path / "ring.gds")
    lib.write_gds(f)
    back = Library.read_gds(f).flatten("TOP")          # default: pieces as stored in the file
    assert total_area(back) == pytest.approx(rg.area, rel=1e-3)
    assert _same_geometry(back, [rg], tol_nm2=rg.area * 2e-3)
    merged = Library.read_gds(f, merge=True).flatten("TOP")   # reassembled outer + hole
    assert len(merged) == 1 and len(merged[0].holes) == 1 and merged[0].is_valid()
    assert merged[0].area == pytest.approx(rg.area, rel=1e-3)


def test_offgrid_vertices_rounded_to_dbu(tmp_path):
    lib = Library("G", dbu_nm=1.0)
    c = lib.new_cell("TOP")
    c.add(polygon([(0.4, 0.4), (100.6, 0.4), (100.6, 50.2), (0.4, 50.2)]))
    f = str(tmp_path / "g.gds")
    lib.write_gds(f)
    p = Library.read_gds(f).flatten("TOP")[0]
    assert np.all(np.abs(p.outer - np.round(p.outer)) < 1e-6)
    assert np.max(np.abs(np.sort(p.outer[:, 0]) - np.sort(c.polygons[0].outer[:, 0]))) <= 0.5 + 1e-9


def test_mop_roundtrip_exact(tmp_path):
    lib, top = _hier_lib()
    lib.cells["TOP"].add(ring(0, 0, 100, 300, tol=1.0, layer=9))
    f = str(tmp_path / "p.mop")
    lib.save_mop(f)
    back = Library.load_mop(f)
    assert set(back.cells) == set(lib.cells)
    a, b = lib.cells["TOP"].flatten(), back.cells["TOP"].flatten()
    assert len(a) == len(b)
    for pa, pb in zip(a, b):
        assert np.allclose(pa.outer, pb.outer) and pa.key == pb.key
        assert len(pa.holes) == len(pb.holes)


def test_mop_rejects_foreign_file(tmp_path):
    f = tmp_path / "x.mop"
    f.write_text('{"format": "other"}')
    with pytest.raises(ValueError):
        Library.load_mop(str(f))


def test_cycle_detected():
    lib = Library()
    a, b = lib.new_cell("A"), lib.new_cell("B")
    a.add_ref(b)
    b.add_ref(a)
    with pytest.raises(ValueError):
        a.flatten()


def test_flatten_needs_unique_top():
    lib = Library()
    lib.new_cell("A")
    lib.new_cell("B")
    with pytest.raises(ValueError):
        lib.flatten()


@pytest.mark.parametrize("rot,xr,mag", [(0, False, 1.0), (90, False, 1.0), (30, False, 1.0), (0, True, 1.0),
                                        (30, True, 1.5), (-45, True, 0.5), (180, False, 2.0)])
def test_array_reference_matches_gdstk_for_all_transforms(tmp_path, rot, xr, mag):
    import gdstk

    lib = Library("A", dbu_nm=0.001)
    u = lib.new_cell("UNIT")
    u.add(rectangle(0, 0, 100, 40))
    top = lib.new_cell("TOP")
    top.add_ref(u, origin=(1000, 2000), rotation=rot, x_reflection=xr, magnification=mag, columns=3, rows=2,
                spacing=(250, 300))
    f = str(tmp_path / "a.gds")
    lib.write_gds(f)
    g = gdstk.read_gds(f)
    gtop = [c for c in g.cells if c.name == "TOP"][0]
    ref = [Polygon(np.asarray(p.points) * 1e3) for p in gtop.get_polygons()]
    assert len(ref) == 6
    assert _same_geometry(top.flatten(), ref, tol_nm2=1.0)
    assert _same_geometry(Library.read_gds(f).flatten("TOP"), ref, tol_nm2=1.0)
