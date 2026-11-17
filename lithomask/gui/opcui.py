"""Phase 9 GUI pieces for OPC: settings dialog, background worker with progress/cancel, and the Compare overlay.

The GUI holds no OPC logic: everything runs through lithomask.workflow (run_opc, apply_to_document, write_verification).
Lengths in dialogs are nm (the optics/process parameters are always nm).
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QProgressDialog, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..geometry import Polygon, bbox_of
from ..workflow import (PRESETS, SOURCES, OPCCancelled, ProcessSettings, RunResult, RunSettings, auto_anchor_cd,
                        layer_counts, polygons_on_layer, run_opc)
from .canvas import poly_path

STAGE_LABELS = (("model", "Model-based (EPE iteration)"), ("rule", "Rule-based (bias / hammerhead)"),
                ("inverse", "Inverse / pixel-based (slow)"))


def _dspin(v: float, lo: float, hi: float, dec: int = 3, step: Optional[float] = None) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setDecimals(dec)
    s.setRange(lo, hi)
    s.setValue(v)
    s.setKeyboardTracking(False)
    if step:
        s.setSingleStep(step)
    return s


class RunOPCDialog(QDialog):
    """Collects a RunSettings.  `doc` supplies the layer list and the target polygons for the CD estimate."""

    def __init__(self, parent, doc, last: Optional[RunSettings] = None):
        super().__init__(parent)
        self.setWindowTitle("Run OPC")
        self.doc = doc
        self.counts = layer_counts(doc)
        s = last or RunSettings()
        p = s.process
        f = QFormLayout(self)

        self.preset = QComboBox()
        self.preset.addItem("(custom)")
        self.preset.addItems(list(PRESETS))
        self.preset.currentIndexChanged.connect(self._apply_preset)
        f.addRow("Process preset", self.preset)

        self.layer = QComboBox()
        for k, n in self.counts.items():
            self.layer.addItem(f"{k[0]}/{k[1]}  ({n} shapes)", k)
        i = self.layer.findData(tuple(s.target_layer))
        self.layer.setCurrentIndex(max(i, 0))
        self.layer.currentIndexChanged.connect(self._refresh_auto)
        f.addRow("Target layer (shapes that should print)", self.layer)

        self.stage = QComboBox()
        for k, label in STAGE_LABELS:
            self.stage.addItem(label, k)
        self.stage.setCurrentIndex(max(self.stage.findData(s.stage), 0))
        f.addRow("OPC method", self.stage)
        self.sraf = QCheckBox("add sub-resolution assist features first")
        self.sraf.setChecked(s.sraf)
        f.addRow("", self.sraf)
        self.iters = QSpinBox()
        self.iters.setRange(1, 200)
        self.iters.setValue(s.max_iter)
        f.addRow("Max iterations (model)", self.iters)
        self.gain = _dspin(s.gain, 0.05, 2.0, 2, 0.05)
        f.addRow("Damping gain (model)", self.gain)

        self.wl = _dspin(p.wavelength, 13.0, 1000.0, 1)
        self.na = _dspin(p.na, 0.05, 1.5, 3, 0.05)
        self.source = QComboBox()
        self.source.addItems(list(SOURCES))
        self.source.setCurrentText(p.source)
        self.sig = _dspin(p.sigma, 0.05, 1.0, 2, 0.05)
        self.sig_in = _dspin(p.sigma_in, 0.0, 0.95, 2, 0.05)
        self.tone = QComboBox()
        self.tone.addItems(["clear_field", "dark_field"])
        self.tone.setCurrentText(p.tone)
        self.shape = QComboBox()
        self.shape.addItems(["line", "hole"])
        self.shape.setCurrentText(p.anchor_shape)
        self.auto = QCheckBox("estimate from target")
        self.auto.setChecked(p.anchor_cd is None)
        self.cd = _dspin(p.anchor_cd or 180.0, 5.0, 1e5, 1)
        self.auto.toggled.connect(self._refresh_auto)
        self.pixel = _dspin(p.pixel, 1.0, 100.0, 1)
        self.margin = _dspin(p.margin, 0.0, 5000.0, 0)
        for label, w in (("Wavelength (nm)", self.wl), ("NA", self.na), ("Source", self.source),
                         ("Sigma / outer sigma", self.sig), ("Inner sigma (annular, dipole, quasar)", self.sig_in),
                         ("Mask tone", self.tone), ("Threshold anchor shape", self.shape),
                         ("Anchor CD (nm): isolated feature that prints on size", self.cd), ("", self.auto),
                         ("Simulation pixel (nm)", self.pixel), ("Simulation margin (nm)", self.margin)):
            f.addRow(label, w)

        row = QHBoxLayout()
        self.ml, self.md = QSpinBox(), QSpinBox()
        self.sl, self.sd = QSpinBox(), QSpinBox()
        for w, v, hi in ((self.ml, s.mask_layer[0], 65535), (self.md, s.mask_layer[1], 255),
                         (self.sl, s.sraf_layer[0], 65535), (self.sd, s.sraf_layer[1], 255)):
            w.setRange(0, hi)
            w.setValue(v)
        row.addWidget(QLabel("mask"))
        row.addWidget(self.ml)
        row.addWidget(self.md)
        row.addWidget(QLabel("  SRAF"))
        row.addWidget(self.sl)
        row.addWidget(self.sd)
        host = QWidget()
        host.setLayout(row)
        f.addRow("Output layer / datatype", host)

        self.note = QLabel()
        self.note.setWordWrap(True)
        f.addRow(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._refresh_auto()

    # ---- helpers ----
    def _target_key(self):
        return self.layer.currentData()

    def _apply_preset(self, idx: int):
        if idx <= 0:
            return
        p = PRESETS[self.preset.currentText()]
        self.wl.setValue(p.wavelength)
        self.na.setValue(p.na)
        self.source.setCurrentText(p.source)
        self.sig.setValue(p.sigma)
        self.sig_in.setValue(p.sigma_in)
        self.tone.setCurrentText(p.tone)
        self.shape.setCurrentText(p.anchor_shape)
        self.pixel.setValue(p.pixel)
        self.margin.setValue(p.margin)

    def _refresh_auto(self, *_):
        self.cd.setEnabled(not self.auto.isChecked())
        if self.auto.isChecked() and self._target_key() is not None:
            try:
                self.cd.setValue(auto_anchor_cd(polygons_on_layer(self.doc, self._target_key())))
            except Exception:
                pass

    def settings(self) -> RunSettings:
        proc = ProcessSettings(self.wl.value(), self.na.value(), self.source.currentText(), self.sig.value(),
                               self.sig_in.value(), self.tone.currentText(), None if self.auto.isChecked() else self.cd.value(),
                               self.shape.currentText(), self.pixel.value(), self.margin.value())
        return RunSettings(proc, self.stage.currentData(), self.sraf.isChecked(), self.iters.value(), self.gain.value(),
                           1.0, 40, tuple(self._target_key() or (1, 0)), (self.ml.value(), self.md.value()),
                           (self.sl.value(), self.sd.value()))

    def _ok(self):
        if self._target_key() is None:
            self.note.setText("The active cell has no shapes to correct.")
            return
        try:
            s = self.settings()
            s.validate()
            s.process.make_imager()
        except Exception as e:
            self.note.setText(f"<b>{e}</b>")
            return
        self.accept()


# ======================================================================================
# Worker + progress
# ======================================================================================
class OPCWorker(QThread):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, polys: List[Polygon], settings: RunSettings, parent=None):
        super().__init__(parent)
        self.polys, self.settings = polys, settings
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _progress(self, stage, it, rms, mx):
        self.progress.emit(f"{stage}: iteration {it}   EPE RMS {rms:.1f} nm, max {mx:.1f} nm")

    def run(self):
        try:
            self.progress.emit("starting ...")
            res = run_opc(self.polys, self.settings, self._progress, lambda: self._cancel)
            self.done.emit(res)
        except OPCCancelled:
            self.cancelled.emit()
        except Exception as e:                      # reported to the user, not swallowed
            self.failed.emit(f"{type(e).__name__}: {e}")


def run_with_progress(parent, polys: List[Polygon], settings: RunSettings) -> "tuple[Optional[RunResult], str]":
    """Run OPC on a worker thread behind a modal progress dialog with Cancel. Returns (result, message); result is None on
    cancel or error (message says which)."""
    dlg = QProgressDialog("starting ...", "Cancel", 0, 0, parent)
    dlg.setWindowTitle("Running OPC")
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setMinimumWidth(460)
    worker = OPCWorker(polys, settings, parent)
    loop = QEventLoop()
    out = {"res": None, "msg": ""}
    worker.progress.connect(dlg.setLabelText)
    worker.done.connect(lambda r: out.update(res=r))
    worker.failed.connect(lambda m: out.update(msg=m))
    worker.cancelled.connect(lambda: out.update(msg="cancelled"))
    worker.finished.connect(loop.quit)
    dlg.canceled.connect(worker.cancel)
    dlg.canceled.connect(lambda: dlg.setLabelText("cancelling after the current step ..."))
    worker.start()
    dlg.show()
    loop.exec()
    worker.wait()
    dlg.canceled.disconnect()
    dlg.close()
    return out["res"], out["msg"]


# ======================================================================================
# Compare overlay
# ======================================================================================
class CompareView(QWidget):
    """Target outline, corrected mask, and printed contours before/after, with wheel zoom and drag pan."""
    LAYERS = (("target", "Target (design intent)", "#46d246"), ("mask", "Corrected mask", "#4a90ff"),
              ("before", "Printed, uncorrected", "#ff5050"), ("after", "Printed, corrected", "#ffd030"))

    def __init__(self, run: RunResult, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 420)
        self.run = run
        self.vis = {k: True for k, _, _ in self.LAYERS}
        data = {"target": run.target, "mask": run.mask, "before": run.print_before.contours,
                "after": run.print_after.contours}
        self.paths = {k: [poly_path(p) for p in v] for k, v in data.items()}
        self.cx, self.cy, self.scale = 0.0, 0.0, 1.0      # set by fit() on first resize
        self._fit_pending = True
        self._drag = None

    def fit(self):
        x0, y0, x1, y1 = bbox_of([p for k in ("target", "mask") for p in
                                  (self.run.target if k == "target" else self.run.mask)])
        w, h = max(x1 - x0, 1.0) * 1.25, max(y1 - y0, 1.0) * 1.25
        self.cx, self.cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        self.scale = min(self.width() / w, self.height() / h)
        self.update()

    def _xf(self) -> QTransform:
        t = QTransform()
        t.translate(self.width() / 2, self.height() / 2)
        t.scale(self.scale, -self.scale)
        t.translate(-self.cx, -self.cy)
        return t

    def resizeEvent(self, ev):
        if self._fit_pending:
            self.fit()
        super().resizeEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#16181d"))
        p.setTransform(self._xf())
        for key, _, col in self.LAYERS:
            if not self.vis[key]:
                continue
            c = QColor(col)
            pen = QPen(c, 2 if key in ("target", "after") else 1.5)
            pen.setCosmetic(True)
            if key == "before":
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            if key == "mask":
                fill = QColor(c)
                fill.setAlpha(70)
                p.setBrush(QBrush(fill))
            else:
                p.setBrush(Qt.NoBrush)
            for path in self.paths[key]:
                p.drawPath(path)
        p.end()

    def wheelEvent(self, ev):
        k = 1.25 if ev.angleDelta().y() > 0 else 0.8
        pos = ev.position()
        inv, _ = self._xf().inverted()
        before = inv.map(QPointF(pos.x(), pos.y()))
        self.scale *= k
        inv2, _ = self._xf().inverted()
        after = inv2.map(QPointF(pos.x(), pos.y()))
        self.cx += before.x() - after.x()
        self.cy += before.y() - after.y()
        self._fit_pending = False
        self.update()

    def mousePressEvent(self, ev):
        self._drag = (ev.position(), self.cx, self.cy)

    def mouseMoveEvent(self, ev):
        if self._drag:
            p0, cx, cy = self._drag
            d = ev.position() - p0
            self.cx, self.cy = cx - d.x() / self.scale, cy + d.y() / self.scale
            self._fit_pending = False
            self.update()

    def mouseReleaseEvent(self, ev):
        self._drag = None


class CompareDialog(QDialog):
    def __init__(self, parent, run: RunResult, report_cb=None):
        super().__init__(parent)
        self.setWindowTitle("Compare: target / corrected mask / printed")
        self.resize(900, 720)
        self.view = CompareView(run)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.boxes = {}
        for key, label, col in CompareView.LAYERS:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet(f"color: {col}; font-weight: bold;")
            cb.toggled.connect(lambda on, k=key: (self.view.vis.__setitem__(k, on), self.view.update()))
            row.addWidget(cb)
            self.boxes[key] = cb
        row.addStretch(1)
        fit = QPushButton("Fit")
        fit.clicked.connect(lambda: (setattr(self.view, "_fit_pending", True), self.view.fit()))
        row.addWidget(fit)
        lay.addLayout(row)
        self.info = QLabel(run.summary())
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        b = QHBoxLayout()
        if report_cb:
            rb = QPushButton("Verification report ...")
            rb.clicked.connect(report_cb)
            b.addWidget(rb)
        b.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        b.addWidget(close)
        lay.addLayout(b)
