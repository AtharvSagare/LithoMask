"""Dock panels: layers, properties (exact numeric editing) and cell tree. They only call Document methods."""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QColorDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import edit
from ..edit import Document
from ..geometry import bbox_of
from .common import UNITS, decimals, fmt
from . import dialogs


class _Bound(QWidget):
    """Base: re-attachable to a Document, refreshes on the listed kinds."""
    kinds: tuple = ()

    def __init__(self, doc: Document, unit_fn: Callable[[], str], parent=None):
        super().__init__(parent)
        self.doc, self.unit_fn, self._busy = doc, unit_fn, False
        doc.listeners.append(self._on_doc)

    def bind(self, doc: Document) -> None:
        if self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)
        self.doc = doc
        doc.listeners.append(self._on_doc)
        self.refresh()

    def _on_doc(self, kind: str) -> None:
        if kind in self.kinds and not self._busy:
            self.refresh()

    def refresh(self) -> None: ...

    def _unit(self) -> str:
        return self.unit_fn()



# ======================================================================================
class LayersPanel(_Bound):
    kinds = ("layers", "content", "file")
    C_VIS, C_LOCK, C_COL, C_LD, C_NAME = range(5)

    def __init__(self, doc, unit_fn, parent=None):
        super().__init__(doc, unit_fn, parent)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Vis", "Lock", "Color", "L/D", "Name"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        for c, w in ((self.C_VIS, 34), (self.C_LOCK, 40), (self.C_COL, 46), (self.C_LD, 56)):
            self.table.setColumnWidth(c, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._dbl)
        self.table.itemSelectionChanged.connect(self._sel_changed)
        add = QPushButton("Add layer...")
        add.clicked.connect(self._add)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.table)
        lay.addWidget(add)
        self._keys: List[tuple] = []
        self.refresh()

    def refresh(self) -> None:
        self._busy = True
        t = self.table
        t.blockSignals(True)
        self._keys = sorted(self.doc.lib.layers)
        t.setRowCount(len(self._keys))
        for r, k in enumerate(self._keys):
            info = self.doc.lib.layers[k]
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if info.visible else Qt.Unchecked)
            t.setItem(r, self.C_VIS, it)
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if info.locked else Qt.Unchecked)
            t.setItem(r, self.C_LOCK, it)
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsEnabled)
            it.setBackground(QBrush(QColor(info.color)))
            t.setItem(r, self.C_COL, it)
            it = QTableWidgetItem(f"{k[0]}/{k[1]}")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            t.setItem(r, self.C_LD, it)
            it = QTableWidgetItem(info.name)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            t.setItem(r, self.C_NAME, it)
            if k == self.doc.current_layer:
                t.selectRow(r)
        t.blockSignals(False)
        self._busy = False

    def _item_changed(self, item) -> None:
        r, c = item.row(), item.column()
        if r >= len(self._keys):
            return
        k = self._keys[r]
        self._busy = True
        try:
            if c == self.C_VIS:
                self.doc.set_layer_info(k, visible=item.checkState() == Qt.Checked)
            elif c == self.C_LOCK:
                self.doc.set_layer_info(k, locked=item.checkState() == Qt.Checked)
            elif c == self.C_NAME:
                self.doc.set_layer_info(k, name=item.text())
        finally:
            self._busy = False

    def _dbl(self, r: int, c: int) -> None:
        if c != self.C_COL or r >= len(self._keys):
            return
        k = self._keys[r]
        col = QColorDialog.getColor(QColor(self.doc.lib.layers[k].color), self, "Layer colour")
        if col.isValid():
            self.doc.set_layer_info(k, color=col.name())
            self.refresh()

    def _sel_changed(self) -> None:
        if self._busy:
            return
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self._keys):
            self._busy = True
            try:
                self.doc.set_current_layer(self._keys[rows[0].row()])
            finally:
                self._busy = False

    def _add(self) -> None:
        txt, ok = QInputDialog.getText(self, "Add layer", "Layer/datatype (e.g. 5/0):", text="10/0")
        if not ok:
            return
        try:
            a, _, b = txt.strip().partition("/")
            key = (int(a), int(b or 0))
            if not (0 <= key[0] <= 65535 and 0 <= key[1] <= 255):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Add layer", "Enter layer/datatype as integers, e.g. 10/0")
            return
        self.doc.set_current_layer(key)
        self.refresh()


# ======================================================================================
class PropertiesPanel(_Bound):
    kinds = ("selection", "content")

    def __init__(self, doc, unit_fn, parent=None):
        super().__init__(doc, unit_fn, parent)
        self.info = QLabel("No selection")
        self.layer = QSpinBox()
        self.layer.setRange(0, 65535)
        self.dtype = QSpinBox()
        self.dtype.setRange(0, 255)
        self.xs, self.ys, self.ws, self.hs = (QDoubleSpinBox() for _ in range(4))
        for s in (self.xs, self.ys, self.ws, self.hs):
            s.setRange(-1e9, 1e9)
            s.setKeyboardTracking(False)
        self.ws.setMinimum(1e-6)
        self.hs.setMinimum(1e-6)
        self.stats = QLabel("")
        self.verts = QTableWidget(0, 3)
        self.verts.setHorizontalHeaderLabels(["ring", "x", "y"])
        self.verts.verticalHeader().setVisible(False)
        self.verts.setColumnWidth(0, 40)
        self.verts.horizontalHeader().setStretchLastSection(True)
        self.verts.itemChanged.connect(self._vertex_changed)
        self.lx, self.ly, self.lw, self.lh = QLabel("X"), QLabel("Y"), QLabel("Width"), QLabel("Height")
        form = QFormLayout()
        form.addRow("Layer", self.layer)
        form.addRow("Datatype", self.dtype)
        form.addRow(self.lx, self.xs)
        form.addRow(self.ly, self.ys)
        form.addRow(self.lw, self.ws)
        form.addRow(self.lh, self.hs)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.info)
        lay.addLayout(form)
        lay.addWidget(self.stats)
        lay.addWidget(QLabel("Vertices (single selection)"))
        lay.addWidget(self.verts, 1)
        self.layer.editingFinished.connect(self._apply_layer)
        self.dtype.editingFinished.connect(self._apply_layer)
        for s in (self.xs, self.ys, self.ws, self.hs):
            s.editingFinished.connect(self._apply_bbox)
        self._bb = (0.0, 0.0, 0.0, 0.0)
        self.refresh()

    def refresh(self) -> None:
        self._busy = True
        sel, u = self.doc.selection, self._unit()
        k, dec = UNITS[u], decimals(u)
        for lab, s in ((self.lx, "X"), (self.ly, "Y"), (self.lw, "Width"), (self.lh, "Height")):
            lab.setText(f"{s} ({u})")
        for s in (self.xs, self.ys, self.ws, self.hs):
            s.setDecimals(dec)
        on = bool(sel)
        for w in (self.layer, self.dtype, self.xs, self.ys, self.ws, self.hs):
            w.setEnabled(on)
        self.verts.blockSignals(True)
        self.verts.setRowCount(0)
        if not on:
            self.info.setText("No selection")
            self.stats.setText("")
            self._bb = (0.0, 0.0, 0.0, 0.0)
        else:
            self._bb = bbox_of(sel)
            x0, y0, x1, y1 = self._bb
            self.xs.setValue(x0 / k)
            self.ys.setValue(y0 / k)
            self.ws.setValue((x1 - x0) / k)
            self.hs.setValue((y1 - y0) / k)
            keys = {p.key for p in sel}
            if len(keys) == 1:
                self.layer.setValue(sel[0].layer)
                self.dtype.setValue(sel[0].datatype)
            self.info.setText(f"{len(sel)} object(s)" + ("" if len(keys) == 1 else f" on {len(keys)} layers"))
            area = sum(p.area for p in sel)
            bad = sum(1 for p in sel if p.check())
            self.stats.setText(f"Area {area / 1e6:.6g} um^2   Vertices {sum(p.num_vertices for p in sel)}"
                               + (f"   WARNING: {bad} invalid" if bad else ""))
            if len(sel) == 1 and sel[0].num_vertices <= 2000:
                p = sel[0]
                rings = [p.outer] + list(p.holes)
                n = sum(len(r) for r in rings)
                self.verts.setRowCount(n)
                row = 0
                for ri, r in enumerate(rings):
                    for vi, (x, y) in enumerate(r):
                        a = QTableWidgetItem(f"{ri}.{vi}")
                        a.setFlags(Qt.ItemIsEnabled)
                        a.setData(Qt.UserRole, (ri, vi))
                        self.verts.setItem(row, 0, a)
                        self.verts.setItem(row, 1, QTableWidgetItem(fmt(x, u) if abs(x) < 1e15 else str(x)))
                        self.verts.setItem(row, 2, QTableWidgetItem(fmt(y, u) if abs(y) < 1e15 else str(y)))
                        row += 1
        self.verts.blockSignals(False)
        self._busy = False

    def _apply_layer(self) -> None:
        if self._busy or not self.doc.selection:
            return
        L, D = self.layer.value(), self.dtype.value()
        if any(p.key != (L, D) for p in self.doc.selection):
            self.doc.set_layer_of_selection(L, D)

    def _apply_bbox(self) -> None:
        if self._busy or not self.doc.selection:
            return
        k = UNITS[self._unit()]
        x, y, w, h = (self.xs.value() * k, self.ys.value() * k, self.ws.value() * k, self.hs.value() * k)
        x0, y0, x1, y1 = self._bb
        if all(abs(a - b) < 1e-4 * max(k / 1000.0, 1e-3) for a, b in ((x, x0), (y, y0), (w, x1 - x0), (h, y1 - y0))):
            return
        try:
            self.doc.apply_to_selection(lambda s: edit.set_bbox(s, x, y, w, h), "Edit position/size")
        except ValueError:
            self.refresh()

    def _vertex_changed(self, item: QTableWidgetItem) -> None:
        if self._busy or len(self.doc.selection) != 1 or item.column() == 0:
            return
        row, k = item.row(), UNITS[self._unit()]
        ri, vi = self.verts.item(row, 0).data(Qt.UserRole)
        try:
            x = float(self.verts.item(row, 1).text()) * k
            y = float(self.verts.item(row, 2).text()) * k
        except ValueError:
            self.refresh()
            return
        p = self.doc.selection[0]
        self._busy = True
        try:
            self.doc.replace([p], [edit.polygon_with_vertex(p, ri, vi, x, y)], "Edit vertex")
        finally:
            self._busy = False
        QTimer.singleShot(0, self.refresh)     # rebuild the table after this signal handler returns


# ======================================================================================
class CellsPanel(_Bound):
    kinds = ("cells", "content", "file")

    def __init__(self, doc, unit_fn, parent=None):
        super().__init__(doc, unit_fn, parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Cells (double-click to open)"])
        self.tree.itemDoubleClicked.connect(self._open)
        self.inst = QListWidget()
        row = QHBoxLayout()
        for text, fn in (("New", self._new), ("Rename", self._rename), ("Delete", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        place = QPushButton("Place instance...")
        place.clicked.connect(self._place)
        rm = QPushButton("Remove instance")
        rm.clicked.connect(self._remove_inst)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.tree, 3)
        lay.addLayout(row)
        lay.addWidget(QLabel("Instances in active cell"))
        lay.addWidget(self.inst, 2)
        r2 = QHBoxLayout()
        r2.addWidget(place)
        r2.addWidget(rm)
        lay.addLayout(r2)
        self.refresh()

    def refresh(self) -> None:
        self._busy = True
        self.tree.clear()
        active = self.doc.cell_name
        bold = QFont()
        bold.setBold(True)

        def add(parent, cell, path):
            it = QTreeWidgetItem(parent if parent is not None else self.tree, [cell.name])
            it.setData(0, Qt.UserRole, cell.name)
            if cell.name == active:
                it.setFont(0, bold)
            seen = set()
            for r in cell.references:
                if r.cell.name not in path and r.cell.name not in seen:
                    seen.add(r.cell.name)
                    add(it, r.cell, path + (cell.name,))
            it.setExpanded(True)

        for c in self.doc.lib.top_cells() or list(self.doc.lib.cells.values())[:1]:
            add(None, c, ())
        self.inst.clear()
        u = self._unit()
        for r in self.doc.cell.references:
            arr = f" [{r.columns}x{r.rows}]" if (r.columns > 1 or r.rows > 1) else ""
            self.inst.addItem(f"{r.cell.name} @ ({fmt(r.origin[0], u)}, {fmt(r.origin[1], u)}) rot {r.rotation:g}{arr}")
        self._busy = False

    def _current(self) -> Optional[str]:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _open(self, item, _col) -> None:
        self.doc.set_cell(item.data(0, Qt.UserRole))

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New cell", "Name:", text="CELL")
        if ok and name.strip():
            self.doc.new_cell(name.strip())

    def _rename(self) -> None:
        old = self._current() or self.doc.cell_name
        name, ok = QInputDialog.getText(self, "Rename cell", "New name:", text=old)
        if ok:
            try:
                self.doc.rename_cell(old, name.strip())
            except ValueError as e:
                QMessageBox.warning(self, "Rename cell", str(e))

    def _delete(self) -> None:
        name = self._current() or self.doc.cell_name
        try:
            self.doc.delete_cell(name)
        except ValueError as e:
            QMessageBox.warning(self, "Delete cell", str(e))

    def _place(self) -> None:
        names = [n for n in self.doc.lib.cells if n != self.doc.cell_name]
        if not names:
            QMessageBox.information(self, "Place instance", "Create another cell first.")
            return
        d = dialogs.ask_place_instance(self, self._unit(), names)
        if d:
            try:
                self.doc.place_reference(**d)
            except ValueError as e:
                QMessageBox.warning(self, "Place instance", str(e))

    def _remove_inst(self) -> None:
        i = self.inst.currentRow()
        if 0 <= i < len(self.doc.cell.references):
            self.doc.remove_reference(self.doc.cell.references[i])
