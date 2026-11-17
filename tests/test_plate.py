"""Mask plate / wafer settings (CleWin5-style presets) and the fit checks."""
import os

import pytest

from lithomask import edit
from lithomask.geometry import rectangle
from lithomask.layout import Library
from lithomask.plate import INCH_MM, MASK_PRESETS, MM, WAFER_PRESETS, PlateSettings, check_plate


def test_presets_match_clewin5():
    assert [k for k in MASK_PRESETS][0] == "Custom"
    sizes = sorted(round(v[0] / INCH_MM) for v in MASK_PRESETS.values() if v)
    assert sizes == [3, 4, 5, 6, 7, 9]
    assert WAFER_PRESETS["2 inch"] == pytest.approx(50.8)
    assert WAFER_PRESETS['100 mm ("4 inch")'] == 100.0
    assert max(WAFER_PRESETS.values()) == 450.0


def test_mask_size_and_label():
    p = PlateSettings(mask_preset="3 inch x 3 inch")
    assert p.mask_size_mm() == pytest.approx((76.2, 76.2))
    assert p.mask_label() == "Mask size: 76mm x 76mm"
    c = PlateSettings(mask_preset="Custom", mask_w_mm=50, mask_h_mm=30)
    assert c.mask_size_mm() == (50.0, 30.0)


def test_layout_outside_mask_is_error():
    p = PlateSettings(show_mask=True, mask_preset="Custom", mask_w_mm=10, mask_h_mm=10)
    ok = [rectangle(-4 * MM, -4 * MM, 4 * MM, 4 * MM)]
    bad = ok + [rectangle(4 * MM, 0, 6 * MM, 1 * MM)]
    assert check_plate(ok, p).ok
    rep = check_plate(bad, p)
    assert rep.n_outside_mask == 1 and rep.issues[0].code == "mask_overflow" and rep.issues[0].severity == "error"


def test_layout_outside_wafer_uses_reduction():
    p = PlateSettings(show_mask=False, show_wafer=True, wafer_preset="2 inch", reduction=1.0)   # radius 25.4 mm
    far = [rectangle(20 * MM, 0, 30 * MM, 1 * MM)]
    assert check_plate(far, p).issues[0].code == "wafer_overflow"
    p.reduction = 4.0                                                           # 30 mm on the mask -> 7.5 mm on the wafer
    assert check_plate(far, p).ok


def test_mask_plate_larger_than_wafer_warns():
    p = PlateSettings(show_mask=True, show_wafer=True, mask_preset="4 inch x 4 inch", wafer_preset="2 inch")
    codes = [i.code for i in check_plate([], p).issues]
    assert codes == ["mask_larger_than_wafer"]
    p.reduction = 5.0                                                           # 101.6/5 = 20.3 mm square fits in 50.8 mm
    assert check_plate([], p).ok


def test_edge_exclusion_and_validation():
    p = PlateSettings(show_wafer=True, wafer_preset="2 inch", edge_exclusion_mm=5)
    assert p.usable_radius_mm() == pytest.approx(20.4)
    with pytest.raises(ValueError):
        PlateSettings(wafer_preset="2 inch", edge_exclusion_mm=30).validate()
    with pytest.raises(ValueError):
        PlateSettings(reduction=0).validate()


def test_document_undo_and_mop_roundtrip(tmp_path):
    doc = edit.Document()
    assert doc.get_plate().show_mask is True and doc.get_plate().show_wafer is True      # both outlines on by default
    doc.set_plate(PlateSettings(show_mask=True, mask_preset="5 inch x 5 inch", show_wafer=True, wafer_preset='150 mm ("6 inch")'))
    assert doc.get_plate().mask_preset == "5 inch x 5 inch"
    path = str(tmp_path / "p.mop")
    doc.save(path)
    d2 = edit.Document.open_file(path)
    assert d2.get_plate().mask_preset == "5 inch x 5 inch" and d2.get_plate().show_wafer
    doc.stack.undo()
    assert doc.get_plate().mask_preset == "4 inch x 4 inch" and doc.get_plate().show_mask is True


def test_gui_plate_menu_and_status():
    from PySide6.QtWidgets import QApplication
    from lithomask.gui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    assert w.act_plate.isEnabled()
    assert w.act_show_mask.isChecked() and w.act_show_wafer.isChecked()          # both ticked from the start
    assert "Mask size" in w.lbl_plate.text() and "Wafer" in w.lbl_plate.text()
    w.doc.set_plate(PlateSettings(show_mask=True, mask_preset="3 inch x 3 inch", show_wafer=True, wafer_preset="2 inch"))
    w._plate_status()
    assert "76mm x 76mm" in w.lbl_plate.text() and "Wafer: 51mm" in w.lbl_plate.text()
    assert w.plate_report().issues[0].code == "mask_larger_than_wafer"
    w.canvas.update(); w.canvas.grab()


def test_units_helpers():
    from lithomask.gui.common import fmt_auto, parse_length
    assert parse_length("250") == 250.0 and parse_length("1 um") == 1000.0 and parse_length("2.5mm") == 2.5e6
    assert parse_length("1 cm") == 1e7 and parse_length("2 inch") == pytest.approx(5.08e7) and parse_length("abc") is None
    assert fmt_auto(250) == "250 nm" and fmt_auto(1500) == "1.5 um" and fmt_auto(2.54e7) == "2.54 cm" and fmt_auto(7e6) == "7 mm"


def test_fit_plate_shows_whole_wafer_and_mask_up_to_1m():
    from PySide6.QtWidgets import QApplication
    from lithomask.gui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1400, 900)
    w.doc.set_plate(PlateSettings(show_mask=True, mask_preset="4 inch x 4 inch", show_wafer=True, wafer_preset="2 inch"))
    assert w.canvas.fit_plate()
    x0, y0, x1, y1 = w.canvas.visible_rect()
    bx = w.canvas.plate_extent()
    assert x0 <= bx[0] and x1 >= bx[2] and y0 <= bx[1] and y1 >= bx[3]
    w.canvas.zoom_center(1e-3)                       # zoom far out: view must be able to span 100 cm = 1e9 nm
    x0, y0, x1, y1 = w.canvas.visible_rect()
    assert (x1 - x0) >= 1e9
    w._view_status()
    assert "Grid lines" in w.lbl_view.text()
    w.grid_combo.setCurrentText("0.5 mm")
    assert w.canvas.grid_nm == 5e5


def test_toggles_draw_visible_outlines():
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
    from lithomask.gui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    app.processEvents()
    w.act_show_mask.setChecked(True); w._toggle_plate("show_mask")
    w.act_show_wafer.setChecked(True); w._toggle_plate("show_wafer")
    assert w.doc.get_plate().show_mask and w.doc.get_plate().show_wafer
    assert "Mask size" in w.lbl_plate.text()
    img = w.canvas.grab().toImage()
    blue = gold = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = QColor(img.pixel(x, y))
            blue += abs(c.red() - 0x4a) < 30 and abs(c.green() - 0xa3) < 30 and abs(c.blue() - 0xff) < 30
            gold += abs(c.red() - 0xd9) < 30 and abs(c.green() - 0xa5) < 30 and abs(c.blue() - 0x20) < 40
    assert blue > 100 and gold > 100, (blue, gold)
    w.act_show_mask.setChecked(False); w._toggle_plate("show_mask")
    assert not w.doc.get_plate().show_mask
    w.doc.stack.undo()
    assert w.act_show_mask.isChecked()
