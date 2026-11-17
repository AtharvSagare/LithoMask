"""Phase 9 GUI piece: Export JEOL52 dialog (Route B package for conversion in BEAMER).

Collects an ExportJob; the actual work is lithomask.export.export_route_b.  Lengths: pattern unit / shot pitch in nm, field and
subfield sizes in um (as in the BEAMER dialog), overlap in um.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QWidget)

from .. import edit
from ..export import DoseTable, FieldOptions, JeolOptions
from ..export.beamer_script import FRACTURE_MODES, MANUAL_EXAMPLE_MACHINE, V52_MACHINES
from ..geometry import Polygon

LayerKey = Tuple[int, int]


@dataclass
class ExportJob:
    layers: List[LayerKey]
    outdir: str
    name: str
    options: JeolOptions
    renumber_to: Optional[LayerKey] = None      # write every exported layer as this layer/datatype


def collect_polygons(polys: List[Polygon], job: ExportJob) -> List[Polygon]:
    """Polygons of the chosen layers, optionally renumbered to one layer.  Dose factors are keyed by layer, so renumbering
    several layers onto one is refused when they carry different dose factors; otherwise the common factor is re-keyed to the
    new layer."""
    keep = [p for p in polys if p.key in set(map(tuple, job.layers))]
    if job.renumber_to is not None:
        facs = {job.options.dose.factor_of(*k) for k in job.layers}
        if len(facs) > 1:
            raise ValueError("cannot renumber layers that have different dose factors onto one layer")
        d = job.options.dose.factor_of(*job.layers[0])
        job.options.dose = DoseTable(job.options.dose.base_dose, {tuple(job.renumber_to): d},
                                     job.options.dose.max_classes, job.options.dose.mode)
        keep = edit.with_layer(keep, *job.renumber_to)
    return keep


class ExportJeolDialog(QDialog):
    def __init__(self, parent, counts: Dict[LayerKey, int], preselect: Optional[List[LayerKey]] = None,
                 default_dir: str = "", default_name: str = "mask"):
        super().__init__(parent)
        self.setWindowTitle("Export JEOL52 (v30) package for BEAMER")
        self.resize(560, 700)
        f = QFormLayout(self)
        intro = QLabel("Writes GDSII + BEAMER job script, settings sheet, dose table, JDF template and a validation "
                       "report. The .v30 itself is made by Layout BEAMER (native .v30 is not available).")
        intro.setWordWrap(True)
        f.addRow(intro)

        self.table = QTableWidget(len(counts), 3)
        self.table.setHorizontalHeaderLabels(["Export", "Layer / datatype (shapes)", "Dose factor"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.keys = list(counts)
        pre = set(map(tuple, preselect or self.keys))
        self.checks, self.doses = [], []
        for r, k in enumerate(self.keys):
            cb = QCheckBox()
            cb.setChecked(k in pre)
            self.table.setCellWidget(r, 0, cb)
            self.table.setItem(r, 1, QTableWidgetItem(f"{k[0]}/{k[1]}  ({counts[k]})"))
            sp = QDoubleSpinBox()
            sp.setRange(0.01, 100.0)
            sp.setDecimals(3)
            sp.setValue(1.0)
            self.table.setCellWidget(r, 2, sp)
            self.checks.append(cb)
            self.doses.append(sp)
        self.table.setColumnWidth(1, 220)
        self.table.setMinimumHeight(140)
        f.addRow(self.table)

        self.renum = QCheckBox("write all exported layers as layer/datatype")
        self.rl, self.rd = QSpinBox(), QSpinBox()
        self.rl.setRange(0, 65535)
        self.rl.setValue(1)
        self.rd.setRange(0, 255)
        row = QHBoxLayout()
        row.addWidget(self.renum)
        row.addWidget(self.rl)
        row.addWidget(self.rd)
        host = QWidget()
        host.setLayout(row)
        f.addRow(host)

        self.outdir = QLineEdit(default_dir)
        br = QPushButton("Browse ...")
        br.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.outdir, 1)
        row.addWidget(br)
        host = QWidget()
        host.setLayout(row)
        f.addRow("Output folder", host)
        self.name = QLineEdit(default_name)
        f.addRow("File base name", self.name)

        self.machine = QComboBox()
        self.machine.setEditable(True)
        self.machine.addItems([MANUAL_EXAMPLE_MACHINE] + [m for m in V52_MACHINES if m != MANUAL_EXAMPLE_MACHINE])
        f.addRow("BEAMER machine type (TODO-VERIFY string)", self.machine)
        self.eos = QLineEdit("1")
        f.addRow("EOS mode (facility specific)", self.eos)
        self.unit = _spin(1.0, 0.1, 100.0, 3)
        self.pitch = _spin(4.0, 0.1, 1000.0, 3)
        self.field = _spin(1000.0, 10.0, 10000.0, 1)
        self.sub = _spin(0.0, 0.0, 1000.0, 3)
        self.overlap = _spin(0.0, 0.0, 5000.0, 2)
        self.base = _spin(300.0, 0.1, 1e5, 2)
        self.mode = QComboBox()
        self.mode.addItems(list(FRACTURE_MODES))
        for label, w in (("Pattern unit (nm)", self.unit), ("Shot pitch (nm; integer multiple of unit)", self.pitch),
                         ("Main field size (um)", self.field), ("Subfield size (um, 0 = none)", self.sub),
                         ("Field overlap, each side (um)", self.overlap), ("Base dose (uC/cm2)", self.base),
                         ("Fracture mode", self.mode)):
            f.addRow(label, w)
        self.note = QLabel()
        self.note.setWordWrap(True)
        f.addRow(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.outdir.text())
        if d:
            self.outdir.setText(d)

    def job(self) -> ExportJob:
        keys = [k for k, c in zip(self.keys, self.checks) if c.isChecked()]
        factors = {k: sp.value() for k, sp in zip(self.keys, self.doses) if abs(sp.value() - 1.0) > 1e-12}
        fields = FieldOptions(field_size=self.field.value() * 1e3,
                              subfield_size=self.sub.value() * 1e3 or None, overlap=self.overlap.value() * 1e3)
        opts = JeolOptions(pattern_unit=self.unit.value(), shot_pitch=self.pitch.value(), fields=fields,
                           dose=DoseTable(base_dose=self.base.value(), factors=factors),
                           machine=self.machine.currentText().strip(), eos_mode=self.eos.text().strip() or "TODO",
                           fracture_mode=self.mode.currentText())
        ren = (self.rl.value(), self.rd.value()) if self.renum.isChecked() else None
        return ExportJob(keys, self.outdir.text().strip(), self.name.text().strip() or "mask", opts, ren)

    def _ok(self):
        j = self.job()
        if not j.layers:
            self.note.setText("<b>Tick at least one layer to export.</b>")
        elif not j.outdir:
            self.note.setText("<b>Choose an output folder.</b>")
        elif not os.path.basename(j.name) == j.name:
            self.note.setText("<b>The base name must not contain a path.</b>")
        elif 2 * j.options.fields.overlap > j.options.fields.field_size:
            self.note.setText("<b>Overlap must be at most half the field size.</b>")
        else:
            self.accept()


def _spin(v: float, lo: float, hi: float, dec: int) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(v)
    s.setKeyboardTracking(False)
    return s
