"""Mask and wafer settings dialog (CleWin5-style: tabs Mask / Wafer) and the overflow warning helper.

All logic lives in lithomask.plate; this file only collects values.  Sizes in mm, centre offsets in mm.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QMessageBox, QTabWidget, QVBoxLayout, QWidget)

from ..plate import MASK_PRESETS, MM, WAFER_PRESETS, PlateReport, PlateSettings


def _spin(v: float, lo: float, hi: float, dec: int = 3, suffix: str = " mm") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setDecimals(dec)
    s.setRange(lo, hi)
    s.setValue(v)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class PlateDialog(QDialog):
    def __init__(self, parent, plate: PlateSettings, layout_report: Optional[PlateReport] = None):
        super().__init__(parent)
        self.setWindowTitle("Mask and wafer settings")
        self.setMinimumWidth(460)
        tabs = QTabWidget()
        # ---- Mask tab ----
        mw = QWidget()
        f = QFormLayout(mw)
        self.show_mask = QCheckBox("Show mask dimensions (outline in background)")
        self.show_mask.setChecked(plate.show_mask)
        f.addRow(self.show_mask)
        self.mask_preset = QComboBox()
        self.mask_preset.addItems(list(MASK_PRESETS))
        self.mask_preset.setCurrentText(plate.mask_preset if plate.mask_preset in MASK_PRESETS else "Custom")
        self.mask_preset.currentTextChanged.connect(self._mask_changed)
        f.addRow("Mask size", self.mask_preset)
        self.mw = _spin(plate.mask_w_mm, 0.1, 2000)
        self.mh = _spin(plate.mask_h_mm, 0.1, 2000)
        f.addRow("Custom width", self.mw)
        f.addRow("Custom height", self.mh)
        self.warn = QCheckBox("Warn about structures outside the mask")
        self.warn.setChecked(plate.warn)
        f.addRow(self.warn)
        tabs.addTab(mw, "Mask")
        # ---- Wafer tab ----
        ww = QWidget()
        g = QFormLayout(ww)
        self.show_wafer = QCheckBox("Show wafer outline")
        self.show_wafer.setChecked(plate.show_wafer)
        g.addRow(self.show_wafer)
        self.wafer_preset = QComboBox()
        self.wafer_preset.addItems(list(WAFER_PRESETS) + ["Custom"])
        self.wafer_preset.setCurrentText(plate.wafer_preset if plate.wafer_preset in WAFER_PRESETS else "Custom")
        self.wafer_preset.currentTextChanged.connect(self._wafer_changed)
        g.addRow("Wafer size", self.wafer_preset)
        self.wd = _spin(plate.wafer_diameter_mm(), 1, 1000)
        g.addRow("Custom diameter", self.wd)
        self.edge = _spin(plate.edge_exclusion_mm, 0, 100)
        g.addRow("Edge exclusion", self.edge)
        self.red = _spin(plate.reduction, 0.1, 100, 2, " : 1")
        g.addRow("Optical reduction (mask : wafer)", self.red)
        tabs.addTab(ww, "Wafer")
        # ---- common ----
        self.cx = _spin(plate.cx_nm / MM, -1000, 1000, 4)
        self.cy = _spin(plate.cy_nm / MM, -1000, 1000, 4)
        cw = QWidget()
        h = QFormLayout(cw)
        h.addRow("Centre X of mask / wafer", self.cx)
        h.addRow("Centre Y of mask / wafer", self.cy)
        tabs.addTab(cw, "Position")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        self.note = QLabel(layout_report.text() if layout_report and layout_report.issues else "")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #c04040;")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._mask_changed(self.mask_preset.currentText())
        self._wafer_changed(self.wafer_preset.currentText())

    def _mask_changed(self, name: str):
        p = MASK_PRESETS.get(name)
        if p:
            self.mw.setValue(p[0]); self.mh.setValue(p[1])
        for w in (self.mw, self.mh):
            w.setEnabled(p is None)

    def _wafer_changed(self, name: str):
        if name in WAFER_PRESETS:
            self.wd.setValue(WAFER_PRESETS[name])
        self.wd.setEnabled(name == "Custom")

    def settings(self) -> PlateSettings:
        return PlateSettings(self.show_mask.isChecked(), self.mask_preset.currentText(), self.mw.value(), self.mh.value(),
                             self.show_wafer.isChecked(), self.wafer_preset.currentText(), self.wd.value(), self.edge.value(),
                             self.red.value(), self.warn.isChecked(), self.cx.value() * MM, self.cy.value() * MM)

    def _ok(self):
        try:
            self.settings().validate()
        except Exception as e:
            self.note.setText(f"<b>{e}</b>")
            return
        self.accept()


def confirm_plate(parent, rep: PlateReport, action: str) -> bool:
    """Show the overflow report; True = user wants to continue anyway.  Returns True immediately when there is nothing to report."""
    if rep.ok:
        return True
    r = QMessageBox.warning(parent, action, rep.text() + f"\n\nContinue with {action} anyway?",
                            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
    return r == QMessageBox.Yes
