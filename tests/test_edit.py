"""Headless tests for lithomask.edit (command stack, document operations, picking)."""
import math

import pytest

from lithomask import edit
from lithomask.edit import Document, UndoStack
from lithomask.geometry import circle, rectangle, total_area


def ids(polys):
    return [id(p) for p in polys]


def test_undo_redo_add_move_delete():
    d = Document()
    r = rectangle(0, 0, 100, 50)
    d.add_polygons([r])
    assert len(d.cell.polygons) == 1 and ids(d.selection) == ids([r])
    d.move_selection(10, 20)
    assert d.cell.polygons[0].bbox == (10, 20, 110, 70)
    d.delete_selection()
    assert d.cell.polygons == []
    assert d.undo() and len(d.cell.polygons) == 1 and d.cell.polygons[0].bbox == (10, 20, 110, 70)
    assert d.undo() and d.cell.polygons[0].bbox == (0, 0, 100, 50)
    assert len(d.selection) == 1  # undo restores the old objects as selection
    assert d.redo() and d.cell.polygons[0].bbox == (10, 20, 110, 70)
    assert d.undo() and d.undo() and d.cell.polygons == [] and not d.stack.can_undo
    assert d.redo() and d.redo() and d.redo() and d.cell.polygons == []


def test_replace_keeps_z_order_and_undo_restores_indices():
    d = Document()
    a, b, c = rectangle(0, 0, 1, 1), rectangle(5, 0, 6, 1), rectangle(10, 0, 11, 1)
    d.add_polygons([a, b, c])
    d.set_selection([a, c])
    d.apply_to_selection(lambda s: edit.moved(s, 0, 100), "Move")
    # replacements are inserted where the first replaced object was; untouched objects keep their relative order
    assert [p.bbox[1] for p in d.cell.polygons] == [100, 100, 0] and d.cell.polygons[2] is b
    d.undo()
    assert d.cell.polygons[0] is a and d.cell.polygons[1] is b and d.cell.polygons[2] is c


def test_redo_branch_discarded_and_dirty_flag():
    d = Document()
    assert not d.is_dirty
    d.add_polygons([rectangle(0, 0, 1, 1)])
    assert d.is_dirty
    d.stack.set_clean()
    assert not d.is_dirty
    d.undo()
    assert d.is_dirty
    d.redo()
    assert not d.is_dirty
    d.undo()
    d.add_polygons([rectangle(0, 0, 2, 2)])  # new branch: the clean state is gone
    d.undo()
    d.redo()
    assert d.is_dirty


def test_macro_is_one_undo_step():
    d = Document()
    with d.macro("two"):
        d.add_polygons([rectangle(0, 0, 1, 1)])
        d.add_polygons([rectangle(2, 0, 3, 1)])
    assert len(d.cell.polygons) == 2
    d.undo()
    assert d.cell.polygons == [] and not d.stack.can_undo
    d.redo()
    assert len(d.cell.polygons) == 2


def test_stack_limit():
    s = UndoStack(limit=3)
    d = Document()
    d.stack = s
    s.listeners.append(d._on_stack)
    for i in range(6):
        d.add_polygons([rectangle(i, 0, i + 1, 1)])
    n = 0
    while s.undo():
        n += 1
    assert n == 3


def test_transforms():
    ps = [rectangle(0, 0, 100, 100)]
    assert edit.rotated(ps, 90)[0].bbox == pytest.approx((0, 0, 100, 100))
    assert edit.rotated(ps, 90, (0, 0))[0].bbox == pytest.approx((-100, 0, 0, 100))
    assert edit.mirrored([rectangle(10, 0, 20, 5)], "y", (0, 0))[0].bbox == pytest.approx((-20, 0, -10, 5))
    assert edit.mirrored([rectangle(0, 10, 5, 20)], "x", (0, 0))[0].bbox == pytest.approx((0, -20, 5, -10))
    assert edit.scaled(ps, 2, 3, (0, 0))[0].bbox == pytest.approx((0, 0, 200, 300))
    with pytest.raises(ValueError):
        edit.scaled(ps, -1)
    out = edit.set_bbox([rectangle(0, 0, 10, 10), rectangle(20, 0, 30, 10)], x=100, y=50, w=60, h=20)
    assert edit.bbox_of(out) == pytest.approx((100, 50, 160, 70))


def test_align_and_distribute():
    ps = [rectangle(0, 0, 10, 10), rectangle(30, 5, 50, 25), rectangle(100, -5, 110, 5)]
    assert [p.bbox[0] for p in edit.aligned(ps, "left")] == [0, 0, 0]
    assert [p.bbox[2] for p in edit.aligned(ps, "right")] == [110, 110, 110]
    assert [p.bbox[3] for p in edit.aligned(ps, "top")] == [25, 25, 25]
    c = [(p.bbox[0] + p.bbox[2]) / 2 for p in edit.aligned(ps, "hcenter")]
    assert c == pytest.approx([55, 55, 55])
    dg = edit.distributed(ps, "x", "gaps")
    gaps = [dg[1].bbox[0] - dg[0].bbox[2], dg[2].bbox[0] - dg[1].bbox[2]]
    assert gaps[0] == pytest.approx(gaps[1]) and dg[0].bbox[0] == 0 and dg[2].bbox[2] == 110
    dc = edit.distributed(ps, "x", "centers")
    cs = [(p.bbox[0] + p.bbox[2]) / 2 for p in dc]
    assert cs[1] - cs[0] == pytest.approx(cs[2] - cs[1])
    dy = edit.distributed(ps, "y", "gaps")
    assert dy[0].bbox[0] == 0 and len(dy) == 3


def test_array_copies_and_document_array():
    d = Document()
    d.add_polygons([rectangle(0, 0, 10, 10)])
    n = d.array_selection(3, 4, 20, 30)
    assert n == 11 and len(d.cell.polygons) == 12
    assert edit.bbox_of(d.cell.polygons) == (0, 0, 70, 70)
    d.undo()
    assert len(d.cell.polygons) == 1


def test_array_as_reference_matches_polygon_array():
    d1, d2 = Document(), Document()
    for d in (d1, d2):
        d.add_polygons([rectangle(0, 0, 10, 10)])
    d1.array_selection(2, 3, 25, 40)
    d2.array_selection(2, 3, 25, 40, as_reference=True)
    f1, f2 = d1.cell.flatten(), d2.cell.flatten()
    assert len(f1) == len(f2) == 6
    assert total_area(f1) == pytest.approx(total_area(f2))
    assert sorted(p.bbox for p in f1) == sorted(p.bbox for p in f2)
    assert len(d2.cell.references) == 1 and len(d2.lib.cells) == 2
    d2.undo()
    assert len(d2.lib.cells) == 1 and len(d2.cell.polygons) == 1 and not d2.cell.references


def test_boolean_and_offset():
    a, b = rectangle(0, 0, 100, 100), rectangle(50, 0, 150, 100)
    assert total_area(edit.boolean_op([a, b], "or")) == pytest.approx(15000)
    assert total_area(edit.boolean_op([a, b], "and")) == pytest.approx(5000)
    assert total_area(edit.boolean_op([a, b], "not")) == pytest.approx(5000)
    assert total_area(edit.boolean_op([a, b], "xor")) == pytest.approx(10000)
    o = edit.offset_polys([a], 10)
    assert total_area(o) == pytest.approx(120 * 120)
    d = Document()
    d.add_polygons([a, b])
    assert d.apply_to_selection(lambda s: edit.boolean_op(s, "or"), "Union") == 1
    assert len(d.cell.polygons) == 1


def test_layer_change_and_layer_info_undo():
    d = Document()
    d.add_polygons([rectangle(0, 0, 1, 1)])
    d.set_layer_of_selection(5, 2)
    assert d.cell.polygons[0].key == (5, 2) and (5, 2) in d.lib.layers
    d.set_layer_info((5, 2), color="#123456", locked=True)
    assert d.lib.layers[(5, 2)].color == "#123456" and d.selection == []
    d.undo()
    assert d.lib.layers[(5, 2)].color != "#123456"
    assert d.selectable(d.cell.polygons[0])


def test_picking():
    a, b = rectangle(0, 0, 100, 100), rectangle(50, 50, 150, 150)
    ps = [a, b]
    assert edit.pick_point(ps, 75, 75, 1) is b          # topmost wins
    assert edit.pick_point(ps, 10, 10, 1) is a
    assert edit.pick_point(ps, 101, 10, 2) is a         # within tolerance of the edge
    assert edit.pick_point(ps, 300, 300, 5) is None
    ring = edit.Polygon(circle(0, 0, 100).outer, [circle(0, 0, 50).outer])
    assert edit.pick_point([ring], 0, 0, 1) is None     # inside the hole
    assert edit.pick_point([ring], 75, 0, 1) is ring
    assert edit.pick_rect(ps, (-10, -10, 110, 110), crossing=False) == [a] or edit.pick_rect(ps, (-10, -10, 110, 110), False)[0] is a
    assert len(edit.pick_rect(ps, (-10, -10, 110, 110), crossing=True)) == 2
    assert len(edit.pick_rect(ps, (-10, -10, 160, 160), crossing=False)) == 2


def test_vertex_edit_and_text_rings():
    p = rectangle(0, 0, 10, 10)
    q = edit.polygon_with_vertex(p, 0, 2, 20, 30)
    assert q.bbox == (0, 0, 20, 30) and p.bbox == (0, 0, 10, 10)
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    inner = [(25, 25), (75, 25), (75, 75), (25, 75)]
    got = edit.rings_to_polygons([outer, inner])
    assert len(got) == 1 and len(got[0].holes) == 1
    assert got[0].area == pytest.approx(10000 - 2500)


def test_cells_place_reference_cycle_and_rename_delete():
    d = Document()
    d.add_polygons([rectangle(0, 0, 10, 10)])
    sub = d.new_cell("SUB", activate=False)
    d.set_cell("SUB")
    d.add_polygons([rectangle(0, 0, 5, 5)])
    d.set_cell("TOP")
    d.place_reference("SUB", origin=(100, 0))
    assert len(d.cell.flatten()) == 2
    d.set_cell("SUB")
    with pytest.raises(ValueError):
        d.place_reference("TOP")        # would be cyclic
    d.set_cell("TOP")
    with pytest.raises(ValueError):
        d.delete_cell("SUB")            # in use
    d.rename_cell("SUB", "LEAF")
    assert "LEAF" in d.lib.cells and d.cell.references[0].cell.name == "LEAF"
    d.undo()
    assert "SUB" in d.lib.cells
    d.set_cell("SUB")
    d.rename_cell("SUB", "S2")
    assert d.cell_name == "S2"           # active cell follows the rename
    d.undo()
    assert d.cell_name == "SUB"


def test_file_round_trips(tmp_path):
    d = Document()
    d.add_polygons([rectangle(0, 0, 1000, 500, 3, 1), circle(5000, 5000, 800)])
    p = str(tmp_path / "a.mop")
    d.save(p)
    assert not d.is_dirty and d.path == p
    d2 = Document.open_file(p)
    assert len(d2.cell.polygons) == 2
    assert d2.cell.polygons[0].bbox == (0, 0, 1000, 500) and (3, 1) in d2.lib.layers
    g = str(tmp_path / "a.gds")
    d.export_gds(g)
    d3 = Document.open_file(g)
    assert total_area(d3.cell.flatten()) == pytest.approx(total_area(d.cell.flatten()), rel=1e-3)
    d.import_gds(g)
    assert len(d.lib.cells) == 2
    with pytest.raises(ValueError):
        Document.open_file(str(tmp_path / "x.txt"))


def test_clipboard_paste():
    d = Document()
    d.add_polygons([rectangle(0, 0, 10, 10)])
    assert d.copy_selection() == 1
    assert d.paste(100, 0) == 1
    assert d.cell.polygons[1].bbox == (100, 0, 110, 10)
