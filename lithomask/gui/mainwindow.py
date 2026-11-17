"""Main window: menus, toolbars, docks, file handling. Delegates all editing to lithomask.edit.Document."""
from __future__ import annotations

import os
from typing import Callable, List, Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QKeySequence, QPainterPath, QTransform
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDockWidget, QDoubleSpinBox, QFileDialog, QGridLayout,
                               QInputDialog, QLabel, QMainWindow, QMessageBox, QToolBar, QWidget)

from .. import __version__, edit, workflow
from ..edit import Document
from ..export import export_route_b, write_cif, write_dxf, write_layer_map
from ..geometry import bbox_of
from . import dialogs, exportui, opcui, plateui
from ..plate import PlateSettings, check_plate
from .canvas import Canvas, Ruler
from .common import (UNITS, app_icon, apply_theme, decimals, fmt, fmt_auto, load_theme, logo_pixmap, parse_length,
                     save_theme)
from .panels import CellsPanel, LayersPanel, PropertiesPanel
from .tools import ToolOptions

FILE_FILTER = "LithoMask / GDSII / OASIS (*.mop *.gds *.gdsii *.oas);;LithoMask project (*.mop);;GDSII (*.gds *.gdsii);;OASIS (*.oas);;All files (*)"
EXPORT_FILTER = "GDSII (*.gds);;OASIS (*.oas);;CIF (*.cif);;DXF (*.dxf)"


def text_rings(text: str, size_nm: float, family: str = "", bold: bool = False) -> List[np.ndarray]:
    """Glyph outlines as closed rings in nm (y up, baseline at y=0, origin at the left of the first glyph).
    Qt supplies only the outlines; combining rings into polygons with holes is edit.rings_to_polygons.
    `size_nm` is the font em size."""
    font = QFont(family or "Arial")
    font.setPixelSize(1000)
    font.setBold(bold)
    path = QPainterPath()
    path.addText(0, 0, font, text)
    k = size_nm / 1000.0
    return [np.array([(pt.x() * k, -pt.y() * k) for pt in poly]) for poly in path.toSubpathPolygons(QTransform())]


class MainWindow(QMainWindow):
    def __init__(self, doc: Optional[Document] = None):
        super().__init__()
        self.options = ToolOptions()
        self.unit = "um"
        self.theme_name = load_theme()       # light on first launch, afterwards the user's last choice
        self.doc = doc or Document()
        self.last_run = None                 # workflow.RunResult of the latest OPC run (None until one succeeds)
        self.last_settings = None            # workflow.RunSettings of the latest run, reused as dialog defaults
        self.last_error = ""                 # message of the latest failed/cancelled run
        self.resize(1400, 850)               # size used only when the user un-maximizes the window
        self.setWindowIcon(app_icon())

        # ---- central: canvas + rulers
        self.canvas = Canvas(self.doc, self.options)
        self.hruler, self.vruler = Ruler(self.canvas, "h"), Ruler(self.canvas, "v")
        corner = QWidget()
        corner.setFixedSize(Ruler.SIZE, Ruler.SIZE)
        central = QWidget()
        g = QGridLayout(central)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(0)
        g.addWidget(corner, 0, 0)
        g.addWidget(self.hruler, 0, 1)
        g.addWidget(self.vruler, 1, 0)
        g.addWidget(self.canvas, 1, 1)
        self.setCentralWidget(central)

        # ---- panels
        uf = lambda: self.unit
        self.layers_panel = LayersPanel(self.doc, uf)
        self.props_panel = PropertiesPanel(self.doc, uf)
        self.cells_panel = CellsPanel(self.doc, uf)
        for title, w, area in (("Layers", self.layers_panel, Qt.RightDockWidgetArea),
                               ("Properties", self.props_panel, Qt.RightDockWidgetArea),
                               ("Cells", self.cells_panel, Qt.LeftDockWidgetArea)):
            d = QDockWidget(title, self)
            d.setWidget(w)
            self.addDockWidget(area, d)

        # ---- canvas callbacks
        ctx = self.canvas.ctx
        ctx.ask_ring_inner = self._ask_ring_inner
        ctx.ask_text = lambda: dialogs.ask_text(self, self.unit)
        ctx.text_rings = text_rings
        ctx.message = lambda s: self.statusBar().showMessage(s, 5000)
        self.canvas.cursor_moved.connect(self._cursor)
        self.canvas.tool_changed.connect(self._tool_changed)
        self.canvas.view_changed.connect(self._status)

        self._build_actions()
        self._build_logo()
        self._build_status()
        self.doc.listeners.append(self._on_doc)
        self._on_doc("file")
        self._on_doc("selection")
        self.canvas.set_tool("select")
        apply_theme(QApplication.instance(), self.theme_name)
        self.canvas.set_theme(self.theme_name)
        QTimer.singleShot(0, self.canvas.fit_plate)

    # ======================================================================== branding
    LOGO_HEIGHT = 36          # logical px, top-left corner of the menu bar

    def _build_logo(self):
        """Logo in the top-left corner (left of the File menu), like other desktop software."""
        self.logo_label = QLabel()
        self.logo_label.setContentsMargins(6, 2, 10, 2)
        self.logo_label.setToolTip("LithoMask")
        self.menuBar().setCornerWidget(self.logo_label, Qt.TopLeftCorner)
        self.logo_label.show()
        self._refresh_logo()

    def _refresh_logo(self):
        pm = logo_pixmap(self.theme_name, self.LOGO_HEIGHT)
        if pm is not None:
            self.logo_label.setPixmap(pm)

    # ======================================================================== actions
    def _act(self, menu, text, slot: Callable, shortcut=None, checkable=False, checked=False, enabled=True, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if checkable:
            a.setChecked(checked)
        a.setEnabled(enabled)
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        a.triggered.connect(lambda *_, f=slot: f())
        menu.addAction(a)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        # -- File
        m = mb.addMenu("&File")
        self._act(m, "&New", self.file_new, "Ctrl+N")
        self._act(m, "&Open...", self.file_open, "Ctrl+O")
        self._act(m, "&Save", self.file_save, "Ctrl+S")
        self._act(m, "Save &As...", self.file_save_as, "Ctrl+Shift+S")
        m.addSeparator()
        self._act(m, "&Import GDSII (add cells)...", self.import_gds)
        self._act(m, "&Export GDSII...", self.export_gds, "Ctrl+E")
        self._act(m, "Export &layout...", self.export_layout)
        m.addSeparator()
        self._act(m, "&Quit", self.close, "Ctrl+Q")
        # -- Edit
        m = mb.addMenu("&Edit")
        self.act_undo = self._act(m, "Undo", lambda: self.doc.undo(), "Ctrl+Z")
        self.act_redo = self._act(m, "Redo", lambda: self.doc.redo(), "Ctrl+Y")
        QShortcutAlias = QAction(self)
        QShortcutAlias.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        QShortcutAlias.triggered.connect(lambda *_: self.doc.redo())
        self.addAction(QShortcutAlias)
        m.addSeparator()
        self._act(m, "Copy", self.do_copy, "Ctrl+C")
        self._act(m, "Paste", self.do_paste, "Ctrl+V")
        self._act(m, "Duplicate", self.do_duplicate, "Ctrl+D")
        self._act(m, "Delete", lambda: self.doc.delete_selection(), "Del")
        m.addSeparator()
        self._act(m, "Select all", lambda: self.doc.select_all(), "Ctrl+A")
        self._act(m, "Deselect", lambda: self.doc.set_selection([]), "Ctrl+Shift+A")

        # -- Draw (tools)
        m = mb.addMenu("&Draw")
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(tb)
        grp = QActionGroup(self)
        grp.setExclusive(True)
        self.tool_actions = {}
        for name, label, key in (("select", "Select / Move", "V"), ("rect", "Rectangle", "R"), ("polygon", "Polygon", "P"),
                                 ("circle", "Circle", "C"), ("ellipse", "Ellipse", "E"), ("ring", "Ring", "O"),
                                 ("wire", "Wire", "W"), ("text", "Text", "T")):
            a = self._act(m, label, lambda n=name: self.canvas.set_tool(n), key, checkable=True,
                          tip=f"{label} ({key})")
            grp.addAction(a)
            tb.addAction(a)
            self.tool_actions[name] = a
        self.tool_actions["select"].setChecked(True)

        # -- Modify
        m = mb.addMenu("&Modify")
        self._act(m, "Move by...", self.do_move)
        self._act(m, "Rotate...", self.do_rotate)
        self._act(m, "Scale...", self.do_scale)
        self._act(m, "Mirror...", self.do_mirror)
        self._act(m, "Array...", self.do_array)
        self._act(m, "Offset / bias...", self.do_offset)
        m.addSeparator()
        bm = m.addMenu("Boolean")
        for op, text in (("or", "Union (all selected)"), ("and", "Intersection (first AND rest)"),
                         ("not", "Subtract (first minus rest)"), ("xor", "XOR (first, rest)")):
            self._act(bm, text, lambda o=op: self.do_boolean(o))
        # -- Arrange
        m = mb.addMenu("&Arrange")
        am = m.addMenu("Align")
        for mode, text in (("left", "Left"), ("hcenter", "Centre horizontally"), ("right", "Right"),
                           ("top", "Top"), ("vcenter", "Centre vertically"), ("bottom", "Bottom")):
            self._act(am, text, lambda s=mode: self.do_align(s))
        dm = m.addMenu("Distribute (3+ objects)")
        for axis, mode, text in (("x", "gaps", "Horizontal gaps"), ("y", "gaps", "Vertical gaps"),
                                 ("x", "centers", "Horizontal centres"), ("y", "centers", "Vertical centres")):
            self._act(dm, text, lambda a=axis, md=mode: self.do_distribute(a, md))

        # -- View
        m = mb.addMenu("&View")
        self._act(m, "Zoom in", lambda: self.canvas.zoom_center(1.5), "Ctrl+=")
        self._act(m, "Zoom out", lambda: self.canvas.zoom_center(1 / 1.5), "Ctrl+-")
        self._act(m, "Fit all", self.canvas.fit_all, "F")
        self.act_fit_plate = self._act(m, "Fit mask / wafer", self.canvas.fit_plate, "W",
                                       tip="Zoom so the whole mask and wafer outline are visible")
        m.addSeparator()
        self.act_grid = self._act(m, "Show grid", self._toggle_grid, "G", checkable=True, checked=True)
        self.act_snap = self._act(m, "Snap to grid", self._toggle_snap, "S", checkable=True, checked=True)
        m.addSeparator()
        self.act_plate = self._act(m, "Mask and wafer settings...", self.mask_wafer_settings, "Ctrl+Shift+M",
                                   tip="Mask plate size and wafer size (like CleWin5 View > Background settings)")
        self.act_show_mask = self._act(m, "Show mask outline", lambda: self._toggle_plate("show_mask"), checkable=True,
                                       tip="Draw the mask plate rectangle in the background")
        self.act_show_wafer = self._act(m, "Show wafer outline", lambda: self._toggle_plate("show_wafer"), checkable=True,
                                        tip="Draw the wafer circle in the background")
        m.addSeparator()
        um = m.addMenu("Display units")
        ug = QActionGroup(self)
        for u in UNITS:
            a = self._act(um, u, lambda u=u: self.set_unit(u), checkable=True, checked=(u == self.unit))
            ug.addAction(a)
        tm = m.addMenu("Theme")
        tg = QActionGroup(self)
        for t in ("dark", "light"):
            a = self._act(tm, t.capitalize(), lambda t=t: self.set_theme(t), checkable=True, checked=(t == self.theme_name))
            tg.addAction(a)

        # -- Run
        m = mb.addMenu("&Run")
        self.act_run = self._act(m, "Run OPC...", self.run_opc, "Ctrl+R",
                                 tip="Correct the shapes of one layer; the result is written to new layers")
        self.act_compare = self._act(m, "Compare target / corrected / printed...", self.compare, "Ctrl+K",
                                     enabled=False, tip="Overlay of the latest OPC run (enabled after a run)")
        self.act_report = self._act(m, "Verification report...", self.verification_report, enabled=False,
                                    tip="HTML/PNG signoff report of the latest OPC run")
        m.addSeparator()
        self.act_jeol = self._act(m, "Export JEOL52...", self.export_jeol52, "Ctrl+Shift+J",
                                  tip="Write the JEOL52 hand-off package (GDSII + BEAMER script/settings)")
        # -- Help
        m = mb.addMenu("&Help")
        self._act(m, "About", self.about)

        # -- options toolbar
        ob = QToolBar("Options")
        ob.setMovable(False)
        self.addToolBar(ob)
        ob.addAction(self.act_undo)
        ob.addAction(self.act_redo)
        ob.addSeparator()
        ob.addWidget(QLabel(" Snap grid "))
        self.grid_combo = QComboBox()
        self.grid_combo.setEditable(True)
        self.grid_combo.setToolTip("Snap spacing. Type a value with a unit (nm, um, mm, cm), e.g. 250 nm or 0.5 mm. No unit = nm.")
        self.grid_combo.addItems(["1 nm", "5 nm", "10 nm", "50 nm", "100 nm", "250 nm", "500 nm", "1 um", "5 um", "10 um",
                                  "100 um", "1 mm", "10 mm", "1 cm"])
        self.grid_combo.setCurrentText("1 um")
        self.grid_combo.setFixedWidth(100)
        self.grid_combo.currentTextChanged.connect(self._grid_changed)
        ob.addWidget(self.grid_combo)
        ob.addAction(self.act_show_mask)
        ob.addAction(self.act_show_wafer)
        ob.addAction(self.act_fit_plate)
        ob.addSeparator()
        ob.addAction(self.act_snap)
        ob.addAction(self.act_grid)
        ob.addSeparator()
        self.wire_label = QLabel(" Wire width ")
        ob.addWidget(self.wire_label)
        self.wire_spin = QDoubleSpinBox()
        self.wire_spin.setRange(1e-7, 1e6)
        self.wire_spin.setKeyboardTracking(False)
        self.wire_spin.valueChanged.connect(self._wire_changed)
        ob.addWidget(self.wire_spin)
        self.wire_end = QComboBox()
        self.wire_end.addItems(["flat", "square", "round"])
        self.wire_end.currentTextChanged.connect(lambda t: setattr(self.options, "wire_end", t))
        ob.addWidget(self.wire_end)
        self.wire_join = QComboBox()
        self.wire_join.addItems(["miter", "round", "bevel"])
        self.wire_join.currentTextChanged.connect(lambda t: setattr(self.options, "wire_join", t))
        ob.addWidget(self.wire_join)
        self._sync_wire_spin()

    def _build_status(self):
        sb = self.statusBar()
        self.lbl_cursor, self.lbl_layer, self.lbl_sel, self.lbl_tool = QLabel(), QLabel(), QLabel(), QLabel()
        self.lbl_plate = QLabel()
        sb.addPermanentWidget(self.lbl_plate)
        self.lbl_view = QLabel()
        sb.addPermanentWidget(self.lbl_view)
        self.canvas.view_changed.connect(self._view_status)
        for w in (self.lbl_tool, self.lbl_layer, self.lbl_sel, self.lbl_cursor):
            w.setMinimumWidth(120)
            sb.addPermanentWidget(w)
        self._cursor(0.0, 0.0)
        self._plate_status()
        self._view_status()

    def _view_status(self):
        c = self.canvas
        w_nm = c.width() / c.scale if c.scale > 0 else 0.0
        self.lbl_view.setText(f"View width {fmt_auto(w_nm)}   Grid lines {fmt_auto(c._grid_step())}   Snap {fmt_auto(c.grid_nm)}")

    # ======================================================================== mask / wafer (CleWin5-style)
    def _plate_status(self):
        p = self.doc.get_plate()
        parts = []
        if p.show_mask:
            parts.append(p.mask_label())
        if p.show_wafer:
            parts.append(f"Wafer: {p.wafer_diameter_mm():.0f}mm @ {p.reduction:g}:1")
        self.lbl_plate.setText("   ".join(parts))
        for act, flag in ((self.act_show_mask, p.show_mask), (self.act_show_wafer, p.show_wafer)):
            act.blockSignals(True)
            act.setChecked(flag)
            act.blockSignals(False)

    def _toggle_plate(self, which: str):
        import dataclasses
        p = self.doc.get_plate()
        on = getattr(self, "act_" + which).isChecked()
        if on and not self.doc.lib.plate:                       # first use: show the sizes you would otherwise have to dial in
            p = dataclasses.replace(p, show_mask=p.show_mask, show_wafer=p.show_wafer)
        self.doc.set_plate(dataclasses.replace(p, **{which: on}))
        self._plate_status()
        if on:
            self.canvas.fit_plate()
        self.canvas.update()

    def plate_report(self):
        return check_plate(self.doc.cell.flatten(), self.doc.get_plate())

    def mask_wafer_settings(self):
        import dataclasses
        cur = self.doc.get_plate()
        if not self.doc.lib.plate:                              # never configured: start with both outlines switched on
            cur = dataclasses.replace(cur, show_mask=True, show_wafer=True)
        dlg = plateui.PlateDialog(self, cur, self.plate_report())
        if dlg.exec() == QDialog.Accepted:
            self.doc.set_plate(dlg.settings())
            self._plate_status()
            if self.doc.get_plate().show_mask or self.doc.get_plate().show_wafer:
                self.canvas.fit_plate()
            self.canvas.update()
            rep = self.plate_report()
            if rep.issues and self.doc.get_plate().warn:
                QMessageBox.warning(self, "Mask and wafer", rep.text())

    def _plate_ok(self, action: str, interactive: bool = True) -> bool:
        """CleWin-style guard: warn (and let the user cancel) when the layout does not fit mask / wafer."""
        if not interactive or not self.doc.get_plate().warn:
            return True
        return plateui.confirm_plate(self, self.plate_report(), action)

    # ======================================================================== status / titles
    def _cursor(self, x: float, y: float):
        u = self.unit
        self.lbl_cursor.setText(f"x {fmt_auto(x)}   y {fmt_auto(y)}")

    def _tool_changed(self, name: str):
        self.lbl_tool.setText(f"Tool: {name}")
        if name in self.tool_actions:
            self.tool_actions[name].setChecked(True)

    def _status(self):
        self.lbl_layer.setText(f"Layer {self.doc.current_layer[0]}/{self.doc.current_layer[1]}")

    def _on_doc(self, kind: str):
        s = self.doc.stack
        if hasattr(self, "lbl_plate"):
            self._plate_status()
        self.act_undo.setText("Undo " + s.undo_text if s.can_undo else "Undo")
        self.act_undo.setEnabled(s.can_undo)
        self.act_redo.setText("Redo " + s.redo_text if s.can_redo else "Redo")
        self.act_redo.setEnabled(s.can_redo)
        name = os.path.basename(self.doc.path) if self.doc.path else "untitled"
        self.setWindowTitle(f"{name}{'*' if self.doc.is_dirty else ''} [{self.doc.cell_name}] - LithoMask")
        self.lbl_sel.setText(f"Selected: {len(self.doc.selection)}")
        self._status()

    # ======================================================================== settings
    def _grid_changed(self, text: str):
        v = parse_length(text, "nm")
        if v is not None and v > 0:
            self.canvas.grid_nm = v
            self.canvas.update()
            self._view_status()

    def _toggle_grid(self):
        self.canvas.show_grid = self.act_grid.isChecked()
        self.canvas.update()

    def _toggle_snap(self):
        self.canvas.snap_on = self.act_snap.isChecked()

    def _sync_wire_spin(self):
        self.wire_spin.blockSignals(True)
        self.wire_spin.setDecimals(decimals(self.unit))
        self.wire_spin.setValue(self.options.wire_width / UNITS[self.unit])
        self.wire_spin.blockSignals(False)
        self.wire_label.setText(f" Wire width ({self.unit}) ")

    def _wire_changed(self, v: float):
        self.options.wire_width = v * UNITS[self.unit]

    def set_unit(self, u: str):
        self.unit = u
        self.canvas.unit = u
        self._sync_wire_spin()
        self.props_panel.refresh()
        self.cells_panel.refresh()
        self.canvas.update()
        self.hruler.update()
        self.vruler.update()
        self._cursor(*self.canvas.cursor_nm)

    def set_theme(self, name: str):
        self.theme_name = name
        apply_theme(QApplication.instance(), name)
        self.canvas.set_theme(name)
        self._refresh_logo()
        save_theme(name)                     # remembered for the next session

    # ======================================================================== document switching / files
    def set_document(self, doc: Document):
        if self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)
        self.canvas.unbind()
        self.doc = doc
        self.canvas.bind(doc)
        for p in (self.layers_panel, self.props_panel, self.cells_panel):
            p.bind(doc)
        doc.listeners.append(self._on_doc)
        self.last_run = None                  # results belong to the previous design
        self._update_run_actions()
        self._on_doc("file")
        self.canvas.fit_all()

    def _maybe_save(self) -> bool:
        if not self.doc.is_dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes before continuing?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        return self.file_save() if r == QMessageBox.Save else True

    def file_new(self):
        if self._maybe_save():
            self.set_document(Document())

    def open_path(self, path: str) -> bool:
        try:
            self.set_document(Document.open_file(path))
            return True
        except Exception as e:
            QMessageBox.critical(self, "Open", f"Could not open {path}:\n{e}")
            return False

    def file_open(self):
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open", "", FILE_FILTER)
        if path:
            self.open_path(path)

    def file_save(self) -> bool:
        if not self.doc.path:
            return self.file_save_as()
        try:
            self.doc.save()
            self.statusBar().showMessage(f"Saved {self.doc.path}", 4000)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save", str(e))
            return False

    def file_save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save As", self.doc.path or "design.mop", "LithoMask project (*.mop)")
        if not path:
            return False
        if not path.lower().endswith(".mop"):
            path += ".mop"
        try:
            self.doc.save(path)
            self.statusBar().showMessage(f"Saved {path}", 4000)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Save", str(e))
            return False

    def import_gds(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import GDSII", "", "GDSII (*.gds *.gdsii);;All files (*)")
        if path:
            try:
                tops = self.doc.import_gds(path)
                self.statusBar().showMessage(f"Imported cells: {', '.join(tops)}", 6000)
                if tops:
                    self.doc.set_cell(tops[0])
                    self.canvas.fit_all()
            except Exception as e:
                QMessageBox.critical(self, "Import GDSII", str(e))

    def export_gds(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export GDSII", "design.gds", "GDSII (*.gds)")
        if path:
            try:
                self.doc.export_gds(path if path.lower().endswith(".gds") else path + ".gds")
                self.statusBar().showMessage(f"Exported {path}", 4000)
            except Exception as e:
                QMessageBox.critical(self, "Export GDSII", str(e))

    def export_layout(self):
        """Export GDSII, OASIS, CIF or DXF; CIF exposes its CleWin-like choices."""
        path, selected = QFileDialog.getSaveFileName(self, "Export layout", "design.gds", EXPORT_FILTER)
        if not path:
            return
        if not self._plate_ok("Export layout"):
            return
        suffix = {"GDSII (*.gds)": ".gds", "OASIS (*.oas)": ".oas", "CIF (*.cif)": ".cif", "DXF (*.dxf)": ".dxf"}
        ext = os.path.splitext(path)[1].lower() or suffix[selected]
        if not os.path.splitext(path)[1]:
            path += ext
        try:
            if ext == ".gds":
                self.doc.export_gds(path)
                if QMessageBox.question(self, "Create layer map", "Create matching .map layer-name sidecar?") == QMessageBox.Yes:
                    write_layer_map(self.doc.lib, os.path.splitext(path)[0] + ".map")
            elif ext in (".oas", ".oasis"):
                self.doc.export_oas(path)
            elif ext == ".cif":
                options = dialogs.ask_cif_options(self)
                if options is None:
                    return
                write_cif(self.doc.lib, path, options=options)
            elif ext == ".dxf":
                write_dxf(self.doc.lib, path)
            else:
                raise ValueError(f"unsupported export extension '{ext}'")
            self.statusBar().showMessage(f"Exported {path}", 4000)
        except Exception as e:
            QMessageBox.critical(self, "Export layout", str(e))

    def closeEvent(self, ev):
        if self._maybe_save():
            ev.accept()
        else:
            ev.ignore()

    # ======================================================================== edit operations
    def _need(self, n: int = 1) -> bool:
        if len(self.doc.selection) < n:
            self.statusBar().showMessage(f"Select at least {n} object(s) first", 4000)
            return False
        return True

    def _step(self) -> float:
        return self.canvas.grid_nm if self.canvas.grid_nm > 0 else 1000.0

    def do_copy(self):
        if self._need():
            self.doc.copy_selection()

    def do_paste(self):
        self.doc.paste(self._step(), -self._step())

    def do_duplicate(self):
        if self._need():
            self.doc.duplicate_selection(self._step(), -self._step())

    def do_move(self):
        if self._need():
            v = dialogs.ask_vector(self, "Move by", self.unit)
            if v:
                self.doc.move_selection(*v)

    def do_rotate(self):
        if self._need():
            r = dialogs.ask_rotate(self)
            if r:
                c = (0.0, 0.0) if r[1] else None
                self.doc.apply_to_selection(lambda s: edit.rotated(s, r[0], c), "Rotate")

    def do_scale(self):
        if self._need():
            r = dialogs.ask_scale(self)
            if r:
                c = (0.0, 0.0) if r[2] else None
                self.doc.apply_to_selection(lambda s: edit.scaled(s, r[0], r[1], c), "Scale")

    def do_mirror(self):
        if self._need():
            r = dialogs.ask_mirror(self)
            if r:
                c = (0.0, 0.0) if r[1] else None
                self.doc.apply_to_selection(lambda s: edit.mirrored(s, r[0], c), "Mirror")

    def do_array(self):
        if self._need():
            x0, y0, x1, y1 = bbox_of(self.doc.selection)
            r = dialogs.ask_array(self, self.unit, x1 - x0, y1 - y0)
            if r:
                self.doc.array_selection(*r)

    def do_offset(self):
        if self._need():
            r = dialogs.ask_offset(self, self.unit)
            if r:
                n = self.doc.apply_to_selection(lambda s: edit.offset_polys(s, r[0], r[1]), "Offset")
                if n == 0:
                    self.statusBar().showMessage("Offset removed all geometry (undo with Ctrl+Z)", 5000)

    def do_boolean(self, op: str):
        if self._need(1 if op == "or" else 2):
            n = self.doc.apply_to_selection(lambda s: edit.boolean_op(s, op), f"Boolean {op}")
            if n == 0:
                self.statusBar().showMessage("Boolean result is empty (undo with Ctrl+Z)", 5000)

    def do_align(self, mode: str):
        if self._need(2):
            self.doc.apply_to_selection(lambda s: edit.aligned(s, mode), "Align")

    def do_distribute(self, axis: str, mode: str):
        if self._need(3):
            self.doc.apply_to_selection(lambda s: edit.distributed(s, axis, mode), "Distribute")

    def _ask_ring_inner(self, r_out_nm: float) -> Optional[float]:
        u = self.unit
        v, ok = QInputDialog.getDouble(self, "Ring", f"Inner radius ({u}); outer = {fmt(r_out_nm, u)}",
                                       r_out_nm / UNITS[u] / 2, 1e-6, r_out_nm / UNITS[u], 4)
        return v * UNITS[u] if ok else None

    # ======================================================================== Phase 9: OPC / compare / export
    def _update_run_actions(self):
        on = self.last_run is not None
        self.act_compare.setEnabled(on)
        self.act_report.setEnabled(on)

    def run_opc(self):
        """Ask for settings, run OPC on a worker thread (progress + cancel), write the mask to new layers, show Compare."""
        if not workflow.layer_counts(self.doc):
            QMessageBox.information(self, "Run OPC", "The active cell is empty - draw the shapes you want to print first.")
            return
        dlg = opcui.RunOPCDialog(self, self.doc, self.last_settings)
        if dlg.exec() == QDialog.Accepted and self._plate_ok("Run OPC"):
            self.run_opc_with(dlg.settings(), interactive=True)

    def run_opc_with(self, settings, interactive: bool = False):
        """Run OPC with explicit settings (no settings dialog). Returns the RunResult, or None on cancel/error (reason in
        self.last_error; shown in a message box when interactive)."""
        polys = workflow.polygons_on_layer(self.doc, settings.target_layer)
        self.last_error = ""
        if not polys:
            self.last_error = f"layer {settings.target_layer[0]}/{settings.target_layer[1]} has no shapes"
            if interactive:
                QMessageBox.warning(self, "Run OPC", self.last_error)
            return None
        self.last_settings = settings
        res, msg = opcui.run_with_progress(self, polys, settings)
        if res is None:
            self.last_error = msg
            if msg == "cancelled":
                self.statusBar().showMessage("OPC cancelled - nothing was changed", 6000)
            elif interactive:
                QMessageBox.critical(self, "Run OPC", msg)
            return None
        self.last_run = res
        n_main, n_sraf = workflow.apply_to_document(self.doc, res)
        self._update_run_actions()
        self.canvas.update()
        self.statusBar().showMessage(f"{res.summary()}  [layers {settings.mask_layer[0]}/{settings.mask_layer[1]}"
                                     f"{', ' + str(settings.sraf_layer[0]) + '/' + str(settings.sraf_layer[1]) if n_sraf else ''}]",
                                     20000)
        if interactive:
            self.compare()
        return res

    def compare(self):
        if self.last_run is None:
            QMessageBox.information(self, "Compare", "Run OPC first.")
            return
        opcui.CompareDialog(self, self.last_run, self.verification_report).exec()

    def verification_report(self):
        if self.last_run is None:
            QMessageBox.information(self, "Verification report", "Run OPC first.")
            return
        d = QFileDialog.getExistingDirectory(self, "Folder for the verification report")
        if not d:
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            paths = self.verification_report_to(d)
        except Exception as e:
            QMessageBox.critical(self, "Verification report", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        QDesktopServices.openUrl(QUrl.fromLocalFile(paths["html"]))
        self.statusBar().showMessage(f"Report written: {paths['html']}", 8000)

    def verification_report_to(self, outdir: str):
        return workflow.write_verification(self.last_run, outdir, name=os.path.splitext(os.path.basename(self.doc.path or "design"))[0])

    def export_jeol52(self):
        counts = workflow.layer_counts(self.doc)
        if not counts:
            QMessageBox.information(self, "Export JEOL52", "The active cell is empty.")
            return
        pre = None
        if self.last_run is not None:
            s = self.last_run.settings
            pre = [k for k in (tuple(s.mask_layer), tuple(s.sraf_layer)) if k in counts] or None
        base = os.path.splitext(os.path.basename(self.doc.path))[0] if self.doc.path else "mask"
        folder = os.path.join(os.path.dirname(self.doc.path) if self.doc.path else os.getcwd(), "jeol_export")
        dlg = exportui.ExportJeolDialog(self, counts, pre, folder, base)
        if dlg.exec() == QDialog.Accepted and self._plate_ok("Export JEOL52"):
            self.export_jeol52_with(dlg.job(), interactive=True)

    def export_jeol52_with(self, job, interactive: bool = False):
        """Write the Route-B package for `job` (an exportui.ExportJob). Returns the ExportReport; errors are shown when
        interactive and re-raised otherwise."""
        try:
            polys = exportui.collect_polygons(self.doc.cell.flatten(), job)
            if not polys:
                raise ValueError("the chosen layers contain no shapes")
            rep = export_route_b(polys, job.outdir, job.name, job.options)
        except Exception as e:
            if not interactive:
                raise
            QMessageBox.critical(self, "Export JEOL52", str(e))
            return None
        self.statusBar().showMessage(f"JEOL52 package written to {job.outdir}", 8000)
        if interactive:
            box = QMessageBox(QMessageBox.Information if rep.ok else QMessageBox.Warning, "Export JEOL52",
                              f"{rep.summary()}\n\nPackage: {job.outdir}\nNext: convert {job.name}.gds to .v30 in Layout BEAMER "
                              f"(see README.md in the folder).", QMessageBox.Open | QMessageBox.Close, self)
            if box.exec() == QMessageBox.Open:
                QDesktopServices.openUrl(QUrl.fromLocalFile(job.outdir))
        return rep

    def about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About LithoMask")
        box.setText(f"LithoMask {__version__}\nPhotomask editor with optical proximity correction, "
                    "print verification and JEOL52 export packages.\nRun menu: Run OPC, Compare, Verification "
                    "report, Export JEOL52.  See USER_GUIDE.md.")
        pm = logo_pixmap(self.theme_name, 120)
        if pm is not None:
            box.setIconPixmap(pm)
        box.exec()
