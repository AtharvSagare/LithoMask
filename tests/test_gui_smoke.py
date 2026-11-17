"""GUI smoke tests (offscreen Qt): real mouse/keyboard events through the canvas and tools, panels, files."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lithomask import edit
from lithomask.edit import Document
from lithomask.gui.mainwindow import MainWindow, text_rings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(app):
    w = MainWindow()
    w.show()
    app.processEvents()
    w.canvas.set_view(6000.0, 4000.0, 0.04)
    yield w
    w.doc.stack._clean = w.doc.stack._idx      # mark clean so close() does not prompt
    w.close()


def P(c, x, y):
    sx, sy = c.to_screen(x, y)
    return QPoint(round(sx), round(sy))


def drag(c, a, b):
    pa, pb = P(c, *a), P(c, *b)
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, pa)
    QTest.mouseMove(c, pb)
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, pb)


def click(c, x, y, mod=Qt.NoModifier):
    QTest.mouseClick(c, Qt.LeftButton, mod, P(c, x, y))


def test_draw_move_edit_save_reload(win, tmp_path):
    c, d = win.canvas, win.doc
    c.set_tool("rect")
    drag(c, (1000, 1000), (5000, 3000))
    assert len(d.cell.polygons) == 1 and d.cell.polygons[0].bbox == (1000, 1000, 5000, 3000)
    assert d.selection and d.selection[0] is d.cell.polygons[0]

    c.set_tool("select")
    click(c, 3000, 2000)
    drag(c, (3000, 2000), (5000, 2000))
    assert d.cell.polygons[0].bbox == (3000, 1000, 7000, 3000)

    # exact numeric edit through the properties panel (units: um)
    win.props_panel.ws.setValue(8.0)
    win.props_panel.ws.editingFinished.emit()
    assert d.cell.polygons[0].bbox == pytest.approx((3000, 1000, 11000, 3000))

    # undo / redo through the real stack
    assert d.undo() and d.cell.polygons[0].bbox == (3000, 1000, 7000, 3000)
    assert d.redo()

    path = str(tmp_path / "t.mop")
    d.save(path)
    d2 = Document.open_file(path)
    assert d2.cell.polygons[0].bbox == pytest.approx((3000, 1000, 11000, 3000))
    win.set_document(d2)
    assert len(win.doc.cell.polygons) == 1 and win.canvas.doc is d2


def test_all_drawing_tools(win):
    c, d = win.canvas, win.doc
    c.set_tool("circle")
    drag(c, (10000, 5000), (12000, 5000))
    assert len(d.cell.polygons) == 1 and d.cell.polygons[0].area == pytest.approx(3.14159 * 2000 ** 2, rel=1e-2)

    c.set_tool("ellipse")
    drag(c, (1000, 6000), (5000, 8000))
    assert d.cell.polygons[-1].bbox == pytest.approx((1000, 6000, 5000, 8000), abs=1.01)

    c.ctx.ask_ring_inner = lambda r: r / 2
    c.set_tool("ring")
    drag(c, (8000, 7000), (10000, 7000))
    assert len(d.cell.polygons[-1].holes) == 1

    c.set_tool("polygon")
    for pt in [(1000, 1000), (4000, 1000), (4000, 3000)]:
        click(c, *pt)
    QTest.keyClick(c, Qt.Key_Return)
    tri = d.cell.polygons[-1]
    assert tri.area == pytest.approx(0.5 * 3000 * 2000) and len(tri.outer) == 3

    c.set_tool("wire")
    win.options.wire_width = 500.0
    for pt in [(1000, 4000), (5000, 4000), (5000, 6000)]:
        click(c, *pt)
    QTest.keyClick(c, Qt.Key_Return)
    wire = d.cell.polygons[-1]
    assert wire.bbox == pytest.approx((1000, 3750, 5250, 6000), abs=1.0)

    # Escape cancels a half-drawn polygon without adding anything
    n = len(d.cell.polygons)
    c.set_tool("polygon")
    click(c, 1000, 1000)
    click(c, 2000, 1000)
    QTest.keyClick(c, Qt.Key_Escape)
    QTest.keyClick(c, Qt.Key_Return)
    assert len(d.cell.polygons) == n


def test_rubber_band_and_multi_ops(win):
    c, d = win.canvas, win.doc
    d.add_polygons([edit.rectangle(1000, 1000, 2000, 2000), edit.rectangle(4000, 1500, 6000, 3000),
                    edit.rectangle(9000, 1000, 10000, 2500)])
    d.set_selection([])
    c.set_tool("select")
    drag(c, (500, 500), (12000, 3500))                  # left-to-right = window select
    assert len(d.selection) == 3
    win.do_align("bottom")
    assert {p.bbox[1] for p in d.cell.polygons} == {1000}
    win.do_distribute("x", "gaps")
    ps = sorted(d.cell.polygons, key=lambda p: p.bbox[0])
    assert ps[1].bbox[0] - ps[0].bbox[2] == pytest.approx(ps[2].bbox[0] - ps[1].bbox[2])
    win.do_boolean("or")
    assert len(d.cell.polygons) == 3                    # disjoint shapes stay three polygons
    # shift-click toggles membership
    d.set_selection([])
    click(c, 1500, 1500)
    click(c, 5000, 1500, Qt.ShiftModifier)
    assert len(d.selection) == 2


def test_panels_follow_document(win):
    d = win.doc
    d.add_polygons([edit.rectangle(0, 0, 1000, 1000, 7, 3)])
    keys = [(win.layers_panel.table.item(r, 3).text()) for r in range(win.layers_panel.table.rowCount())]
    assert "7/3" in keys
    assert win.props_panel.verts.rowCount() == 4
    d.new_cell("SUB")
    assert win.cells_panel.tree.topLevelItemCount() >= 1
    win.canvas.grab()                                   # a full paint pass must not raise
    d.set_cell("TOP")


def test_layer_visibility_hides_and_locks_picking(win):
    c, d = win.canvas, win.doc
    d.add_polygons([edit.rectangle(1000, 1000, 3000, 3000, 4, 0)])
    d.set_selection([])
    d.set_layer_info((4, 0), locked=True)
    c.set_tool("select")
    click(c, 2000, 2000)
    assert d.selection == []
    d.set_layer_info((4, 0), locked=False)
    click(c, 2000, 2000)
    assert len(d.selection) == 1


def test_text_tool_and_reserved_menu_entries(win):
    rings = text_rings("AB", 4000.0, "")
    if not rings:
        pytest.skip("no fonts available in this Qt platform")
    polys = edit.rings_to_polygons(rings)
    assert polys and max(p.bbox[3] for p in polys) > 1000
    reserved = {a.text(): a for a in win.findChildren(QAction) if a.text().startswith(("Run OPC", "Export JEOL52"))}
    assert len(reserved) == 2 and all(a.isEnabled() for a in reserved.values())   # wired in Phase 9


def test_zoom_pan_and_snap(win):
    c = win.canvas
    p0 = c.to_scene(200, 150)
    c.zoom_at(2.0, 200, 150)
    p1 = c.to_scene(200, 150)
    assert p1 == pytest.approx(p0)                      # point under the cursor stays fixed
    c.snap_on = True
    c.grid_nm = 500.0
    assert c.snap(1234.0, -760.0) == (1000.0, -1000.0)
    c.snap_on = False
    assert c.snap(1234.0, -760.0) == (1234.0, -760.0)
    c.snap_on = True
    c.fit_all()
