import json
import pytest
import gdstk
from lithomask import rectangle, polygon, circle, ring, Library
from lithomask.export import (fracture_polygons, partition, FieldOptions, DoseTable, JeolOptions,
                            prepare, export_route_b, write_v30)


def test_rect_single_trap():
    r = fracture_polygons([rectangle(0, 0, 100, 50)])
    assert len(r.traps) == 1 and r.traps[0].is_rect
    assert r.max_rel_area_error < 1e-12


def test_l_shape_area_and_merge():
    p = polygon([(0, 0), (200, 0), (200, 100), (100, 100), (100, 300), (0, 300)])
    r = fracture_polygons([p])
    assert sum(t.area for t in r.traps) == pytest.approx(p.area, rel=1e-12)
    assert all(t.is_rect for t in r.traps)
    assert len(r.traps) == 2          # vertical rect merged, plus the foot


def test_trapezoid_triangle_slanted():
    p = polygon([(0, 0), (100, 0), (50, 100)])
    r = fracture_polygons([p])
    assert len(r.traps) == 1 and not r.traps[0].is_rect
    assert r.traps[0].area == pytest.approx(5000.0)


def test_ring_with_hole_area():
    p = ring(0, 0, 100, 200, tol=0.5)
    r = fracture_polygons([p], pattern_unit=1.0)
    assert r.max_rel_area_error < 5e-3
    assert sum(t.area for t in r.traps) == pytest.approx(p.area, rel=2e-2)


def test_circle_area_vs_pi_r2():
    r = fracture_polygons([circle(0, 0, 500, tol=0.5)], pattern_unit=1.0)
    assert sum(t.area for t in r.traps) == pytest.approx(3.14159265 * 500**2, rel=5e-3)


def test_snap_to_pattern_unit_matches_note_example():
    r = fracture_polygons([rectangle(0, 0, 10, 130)], pattern_unit=4.0)   # 10 nm -> 12 nm on a 4 nm unit
    t = r.traps[0]
    assert (t.xr0 - t.xl0) % 4 == 0 and t.xr0 - t.xl0 == pytest.approx(12.0)


def test_overlapping_shapes_not_double_exposed():
    r = fracture_polygons([rectangle(0, 0, 100, 100), rectangle(50, 0, 150, 100)])
    assert sum(t.area for t in r.traps) == pytest.approx(15000.0)


def test_layers_kept_separate():
    r = fracture_polygons([rectangle(0, 0, 10, 10, layer=1), rectangle(0, 0, 10, 10, layer=2)])
    assert {t.layer for t in r.traps} == {1, 2}


def test_field_partition_cuts_and_conserves_area():
    p = rectangle(0, 0, 3000, 500)
    plan = partition([p], 1.0, FieldOptions(field_size=1000.0))
    assert len(plan.fields) == 3 and plan.cut_shapes == 1
    assert sum(t.area for t in plan.traps) == pytest.approx(p.area)
    assert [f.i for f in plan.fields] == [0, 1, 2]


def test_field_overlap_avoids_cut():
    big = rectangle(0, 0, 2000, 100)  # defines extent of two fields (itself cut)
    q = rectangle(900, 0, 1100, 100, layer=2)  # straddles boundary at x=1000
    cut = partition([q, big], 1.0, FieldOptions(field_size=1000.0, overlap=0.0))
    ov = partition([q, big], 1.0, FieldOptions(field_size=1000.0, overlap=150.0))
    assert cut.cut_shapes == 2 and ov.cut_shapes == 1  # only `big` is cut with overlap


def test_first_field_is_upper_left():
    plan = partition([rectangle(0, 0, 2500, 2500)], 1.0, FieldOptions(field_size=1000.0))
    f0 = plan.fields[0]
    assert (f0.i, f0.j) == (0, 0) and f0.x0 == 0 and f0.y_top == 2500


def test_dose_classes_and_jdi():
    d = DoseTable(base_dose=300.0, factors={1: 1.0, 2: 1.2})
    keys = [(1, 0), (2, 0)]
    cmap = d.classes(keys)
    assert sorted(cmap.values()) == [0, 1]
    s = d.to_jdi(keys)
    assert "MODULAT" in s and "300.00" in s and "360.00" in s
    with pytest.raises(ValueError):
        DoseTable(max_classes=1, factors={1: 1.0, 2: 1.2}).classes(keys)


def test_prepare_report_and_pitch_warning():
    _, rep = prepare([rectangle(0, 0, 1000, 200)], JeolOptions(pattern_unit=1.0, shot_pitch=4.0))
    assert rep.ok and rep.n_traps == 1 and rep.total_area_nm2 == pytest.approx(200000.0)
    assert rep.n_shots_est == 250 * 50
    _, bad = prepare([rectangle(0, 0, 100, 100)], JeolOptions(pattern_unit=1.5, shot_pitch=4.0))
    assert not bad.ok


def test_route_b_files(tmp_path):
    lib = Library("T")
    lib.new_cell("TOP").add(rectangle(0, 0, 1000, 200), circle(2000, 100, 100, tol=1.0))
    rep = export_route_b(lib, str(tmp_path), "demo", JeolOptions())
    for f in ["demo.gds", "demo_fractured_preview.gds", "demo.jdi", "demo_beamer_settings.txt",
              "demo.jdf.template", "README.md", "demo_report.json"]:
        assert (tmp_path / f).exists(), f
    assert json.loads((tmp_path / "demo_report.json").read_text())["n_traps"] == rep.n_traps
    readme = (tmp_path / "README.md").read_text()
    assert "demo.v30" in readme and "<job_name>.jdf" in readme and "<schedule_name>.sdf" in readme
    g = gdstk.read_gds(str(tmp_path / "demo.gds"))
    assert len(g.cells) >= 1


def _script(**kw):
    from lithomask.export import build_beamer_script
    o = JeolOptions(dose=DoseTable(factors={2: 1.2}), **kw)
    return build_beamer_script("demo", o, [(1, 0), (2, 0)])


def test_beamer_script_is_py26_safe_and_has_manual_params():
    import ast
    s = _script()
    tree = ast.parse(s)
    assert not any(isinstance(n, (ast.JoinedStr, ast.AnnAssign, ast.NamedExpr)) for n in ast.walk(tree))
    for key in ("export_v30", "import_gds", "heal", "fda", "'MachineType'", "'FieldSizeX'", "'FieldOverlapY'",
                "'FractureMode': 'Conventional'", "'PreserveDoseRange': 'true'", "'2(0)'", "'Assign Value'",
                "set_number_of_threads"):
        assert key in s, key
    assert "'1(0)'" not in s            # factor 1.0 layers are not sent to FDA
    assert ".format(" not in s


def test_beamer_script_options_and_validation():
    s = _script(fracture_mode="LRFT", eos_mode="3")
    assert "'FractureMode': 'LRFT'" in s and "'EOSMode': '3'" in s
    assert "'EOSMode': '1'" in _script()       # default flagged TODO-VERIFY
    with pytest.raises(ValueError):
        _script(fracture_mode="bogus")


def test_route_b_writes_beamer_job(tmp_path):
    lib = Library("T")
    lib.new_cell("TOP").add(rectangle(0, 0, 1000, 200))
    export_route_b(lib, str(tmp_path), "demo", JeolOptions())
    job = tmp_path / "demo_beamer_job.py"
    assert job.exists()
    compile(job.read_text(), str(job), "exec")
    assert "demo_beamer_job.py" in (tmp_path / "README.md").read_text()


def test_route_a_blocked():
    with pytest.raises(NotImplementedError):
        write_v30()
