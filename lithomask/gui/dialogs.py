"""Modal dialogs. All lengths are entered in the current display unit and returned in nm."""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFontComboBox,
                               QFormLayout, QLineEdit, QSpinBox, QWidget)

from .common import UNITS, decimals


def _dspin(value: float = 0.0, lo: float = -1e9, hi: float = 1e9, dec: int = 4) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setDecimals(dec)
    s.setRange(lo, hi)
    s.setValue(value)
    s.setKeyboardTracking(False)
    return s


class FormDialog(QDialog):
    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.form = QFormLayout(self)
        self._bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._bb.accepted.connect(self.accept)
        self._bb.rejected.connect(self.reject)

    def add(self, label: str, w: QWidget) -> QWidget:
        self.form.addRow(label, w)
        return w

    def run(self) -> bool:
        self.form.addRow(self._bb)
        return self.exec() == QDialog.Accepted


def ask_cif_options(parent: QWidget):
    """Return CleWin-like CIF Save As choices, or None when cancelled."""
    from ..export import CifOptions

    d = FormDialog("CIF export options", parent)
    ext = d.add("Use CleWin extensions", QCheckBox())
    flat = d.add("Flatten file structure", QCheckBox())
    empty = d.add("Remove empty symbol definitions", QCheckBox())
    unused = d.add("Remove unused symbol definitions", QCheckBox())
    flat.setChecked(True)
    empty.setChecked(True)
    unused.setChecked(True)
    if not d.run():
        return None
    return CifOptions(ext.isChecked(), flat.isChecked(), empty.isChecked(), unused.isChecked())


def _decimals(unit: str) -> int:
    return decimals(unit)


def ask_vector(parent, title: str, unit: str, labels=("dx", "dy")) -> Optional[Tuple[float, float]]:
    d = FormDialog(title, parent)
    a = d.add(f"{labels[0]} ({unit})", _dspin(0.0, dec=_decimals(unit)))
    b = d.add(f"{labels[1]} ({unit})", _dspin(0.0, dec=_decimals(unit)))
    if not d.run():
        return None
    return a.value() * UNITS[unit], b.value() * UNITS[unit]


def ask_rotate(parent) -> Optional[Tuple[float, bool]]:
    """-> (angle_deg, about_origin)"""
    d = FormDialog("Rotate", parent)
    a = d.add("Angle (deg, CCW)", _dspin(90.0, dec=4))
    c = d.add("About", QComboBox())
    c.addItems(["Selection centre", "Origin (0, 0)"])
    return (a.value(), c.currentIndex() == 1) if d.run() else None


def ask_scale(parent) -> Optional[Tuple[float, float, bool]]:
    """-> (sx, sy, about_origin)"""
    d = FormDialog("Scale", parent)
    sx = d.add("Scale X", _dspin(1.0, 1e-6, 1e6, 6))
    sy = d.add("Scale Y", _dspin(1.0, 1e-6, 1e6, 6))
    uni = d.add("Uniform (use X for both)", QCheckBox())
    uni.setChecked(True)
    c = d.add("About", QComboBox())
    c.addItems(["Selection centre", "Origin (0, 0)"])
    if not d.run():
        return None
    x = sx.value()
    return x, (x if uni.isChecked() else sy.value()), c.currentIndex() == 1


def ask_mirror(parent) -> Optional[Tuple[str, bool]]:
    """-> (axis 'x'|'y', about_origin); 'x' flips up/down, 'y' flips left/right."""
    d = FormDialog("Mirror", parent)
    a = d.add("Flip", QComboBox())
    a.addItems(["Up / down (about a horizontal axis)", "Left / right (about a vertical axis)"])
    c = d.add("About", QComboBox())
    c.addItems(["Selection centre", "Origin (0, 0)"])
    return ("x" if a.currentIndex() == 0 else "y", c.currentIndex() == 1) if d.run() else None


def ask_array(parent, unit: str, width_nm: float, height_nm: float):
    """-> (rows, cols, dx_nm, dy_nm, as_reference)"""
    d = FormDialog("Array", parent)
    rows, cols = QSpinBox(), QSpinBox()
    for s, v in ((rows, 2), (cols, 2)):
        s.setRange(1, 10000)
        s.setValue(v)
    d.add("Rows", rows)
    d.add("Columns", cols)
    k = UNITS[unit]
    dx = d.add(f"Pitch X ({unit})", _dspin((width_nm + 1000.0) / k, dec=_decimals(unit)))
    dy = d.add(f"Pitch Y ({unit})", _dspin((height_nm + 1000.0) / k, dec=_decimals(unit)))
    ref = d.add("Create as cell array reference", QCheckBox())
    if not d.run():
        return None
    return rows.value(), cols.value(), dx.value() * k, dy.value() * k, ref.isChecked()


def ask_offset(parent, unit: str) -> Optional[Tuple[float, str]]:
    d = FormDialog("Offset / bias", parent)
    v = d.add(f"Distance ({unit}); negative shrinks", _dspin(100.0 / UNITS[unit], dec=_decimals(unit)))
    j = d.add("Corners", QComboBox())
    j.addItems(["miter", "round", "bevel"])
    return (v.value() * UNITS[unit], j.currentText()) if d.run() else None


def ask_text(parent, unit: str) -> Optional[dict]:
    d = FormDialog("Text", parent)
    t = d.add("Text", QLineEdit("MASK"))
    h = d.add(f"Font size ({unit})", _dspin(10000.0 / UNITS[unit], 1e-6, 1e9, _decimals(unit)))
    f = d.add("Font", QFontComboBox())
    b = d.add("Bold", QCheckBox())
    if not d.run() or not t.text():
        return None
    return {"text": t.text(), "height": h.value() * UNITS[unit], "family": f.currentFont().family(), "bold": b.isChecked()}


def ask_place_instance(parent, unit: str, cells: List[str]) -> Optional[dict]:
    d = FormDialog("Place instance", parent)
    c = d.add("Cell", QComboBox())
    c.addItems(cells)
    k, dec = UNITS[unit], _decimals(unit)
    x = d.add(f"X ({unit})", _dspin(0.0, dec=dec))
    y = d.add(f"Y ({unit})", _dspin(0.0, dec=dec))
    rot = d.add("Rotation (deg)", _dspin(0.0, dec=3))
    mir = d.add("Mirror (x-reflection)", QCheckBox())
    mag = d.add("Magnification", _dspin(1.0, 1e-6, 1e6, 6))
    cols, rows = QSpinBox(), QSpinBox()
    for s in (cols, rows):
        s.setRange(1, 10000)
    d.add("Columns", cols)
    d.add("Rows", rows)
    px = d.add(f"Pitch X ({unit})", _dspin(0.0, dec=dec))
    py = d.add(f"Pitch Y ({unit})", _dspin(0.0, dec=dec))
    if not d.run():
        return None
    return dict(target=c.currentText(), origin=(x.value() * k, y.value() * k), rotation=rot.value(),
                x_reflection=mir.isChecked(), magnification=mag.value(), columns=cols.value(), rows=rows.value(),
                spacing=(px.value() * k, py.value() * k))
