"""Canvas widget (pan/zoom/grid/snap, painting, mouse routing) and rulers."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QKeyEvent, QPainter, QPainterPath, QPen, QPolygonF, QTransform)
from PySide6.QtWidgets import QWidget

from ..edit import Document
from ..geometry import Polygon
from ..layout import Cell
from .common import THEMES, Theme, fmt, fmt_auto
from .tools import TOOLS, SelectTool, Tool, ToolContext, ToolOptions

MIN_SCALE, MAX_SCALE = 1e-8, 200.0     # px per nm: 1e-8 shows ~100 m across a window, 200 shows ~5 nm


def poly_path(p: Polygon) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for r in [p.outer] + list(p.holes):
        path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in r]))
        path.closeSubpath()
    return path


def nice_step(min_nm: float) -> float:
    """Smallest 1-2-5 x 10^k step >= min_nm."""
    e = 10.0 ** math.floor(math.log10(max(min_nm, 1e-12)))
    for m in (1, 2, 5, 10):
        if m * e >= min_nm:
            return m * e
    return 10 * e


class Canvas(QWidget):
    cursor_moved = Signal(float, float)    # snapped scene position, nm
    view_changed = Signal()
    tool_changed = Signal(str)

    def __init__(self, doc: Document, options: Optional[ToolOptions] = None, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(300, 200)
        self.doc = doc
        self.options = options or ToolOptions()
        self.center = [0.0, 0.0]
        self.scale = 0.05                                   # px per nm (1 um = 50 px)
        self.grid_nm = 1000.0
        self.snap_on = True
        self.show_grid = True
        self.unit = "um"
        self.theme: Theme = THEMES["dark"]
        self.cursor_nm = (0.0, 0.0)
        self.ctx = ToolContext(doc, self.options)
        self.ctx.snap = self.snap
        self.tool: Tool = SelectTool(self.ctx)
        self._pan: Optional[Tuple[QPointF, List[float]]] = None
        self._space = False
        self._paths: Dict[int, Tuple[Polygon, QPainterPath]] = {}
        self._refs: List[Tuple[Polygon, QPainterPath]] = []
        self.bind(doc)

    # ---- document binding ----------------------------------------------------------
    def bind(self, doc: Document) -> None:
        self.doc = doc
        self.ctx.doc = doc
        doc.listeners.append(self._on_doc)
        self.tool.cancel()
        self._rebuild()

    def unbind(self) -> None:
        if self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)

    def _on_doc(self, kind: str) -> None:
        if kind in ("content", "cells"):
            self._rebuild()
        self.update()

    def _rebuild(self) -> None:
        old = self._paths
        self._paths = {}
        for p in self.doc.cell.polygons:
            hit = old.get(id(p))
            self._paths[id(p)] = hit if hit is not None else (p, poly_path(p))
        refs = self.doc.cell.references
        self._refs = []
        if refs:
            try:
                tmp = Cell("_view", [], list(refs))
                self._refs = [(p, poly_path(p)) for p in tmp.flatten()]
            except Exception:
                self._refs = []

    # ---- view transform ------------------------------------------------------------
    def _xform(self) -> QTransform:
        s = self.scale
        return QTransform(s, 0, 0, -s, self.width() / 2 - self.center[0] * s, self.height() / 2 + self.center[1] * s)

    def to_scene(self, px: float, py: float) -> Tuple[float, float]:
        s = self.scale
        return (self.center[0] + (px - self.width() / 2) / s, self.center[1] - (py - self.height() / 2) / s)

    def to_screen(self, x: float, y: float) -> Tuple[float, float]:
        s = self.scale
        return ((x - self.center[0]) * s + self.width() / 2, -(y - self.center[1]) * s + self.height() / 2)

    def visible_rect(self) -> Tuple[float, float, float, float]:
        x0, y1 = self.to_scene(0, 0)
        x1, y0 = self.to_scene(self.width(), self.height())
        return x0, y0, x1, y1

    def _changed(self) -> None:
        self.ctx.px_per_nm = self.scale
        self.view_changed.emit()
        self.update()

    def set_view(self, cx: float, cy: float, scale: float) -> None:
        self.center = [cx, cy]
        self.scale = min(max(scale, MIN_SCALE), MAX_SCALE)
        self._changed()

    def zoom_at(self, factor: float, px: float, py: float) -> None:
        x, y = self.to_scene(px, py)
        s = min(max(self.scale * factor, MIN_SCALE), MAX_SCALE)
        self.scale = s
        self.center = [x - (px - self.width() / 2) / s, y + (py - self.height() / 2) / s]
        self._changed()

    def zoom_center(self, factor: float) -> None:
        self.zoom_at(factor, self.width() / 2, self.height() / 2)

    def fit_all(self) -> None:
        bb = self.doc.cell.bbox()
        if self._refs:
            from ..geometry import bbox_of
            rb = bbox_of([p for p, _ in self._refs])
            bb = rb if bb is None else (min(bb[0], rb[0]), min(bb[1], rb[1]), max(bb[2], rb[2]), max(bb[3], rb[3]))
        if bb is None:
            if not self.fit_plate():
                self.set_view(0.0, 0.0, 0.05)
            return
        w, h = max(bb[2] - bb[0], 1.0), max(bb[3] - bb[1], 1.0)
        s = min(self.width() / (w * 1.15), self.height() / (h * 1.15))
        self.set_view((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, s)

    def plate_extent(self) -> Optional[Tuple[float, float, float, float]]:
        """Bounding box (nm) of the visible mask rectangle and wafer circle (wafer drawn at mask scale), or None."""
        from ..plate import PlateSettings
        try:
            pl = PlateSettings.from_dict(self.doc.lib.plate)
            pl.validate()
        except Exception:
            return None
        boxes = []
        if pl.show_mask:
            boxes.append(pl.mask_rect_nm())
        if pl.show_wafer:
            r = pl.wafer_radius_nm() * pl.reduction
            boxes.append((pl.cx_nm - r, pl.cy_nm - r, pl.cx_nm + r, pl.cy_nm + r))
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    def fit_plate(self) -> bool:
        """Zoom to the whole mask / wafer outline (and the design if it sticks out). False if neither outline is shown."""
        bb = self.plate_extent()
        if bb is None:
            return False
        cb = self.doc.cell.bbox()
        if cb is not None:
            bb = (min(bb[0], cb[0]), min(bb[1], cb[1]), max(bb[2], cb[2]), max(bb[3], cb[3]))
        w, h = max(bb[2] - bb[0], 1.0), max(bb[3] - bb[1], 1.0)
        s = min(self.width() / (w * 1.15), self.height() / (h * 1.15))
        self.set_view((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, s)
        return True

    def snap(self, x: float, y: float) -> Tuple[float, float]:
        if not self.snap_on or self.grid_nm <= 0:
            return x, y
        g = self.grid_nm
        return round(x / g) * g, round(y / g) * g

    # ---- tools -----------------------------------------------------------------------
    def set_tool(self, name: str) -> None:
        self.tool.cancel()
        self.tool = TOOLS[name](self.ctx)
        self.setCursor(Qt.ArrowCursor if name == "select" else Qt.CrossCursor)
        self.tool_changed.emit(name)
        self.update()

    # ---- painting --------------------------------------------------------------------
    def set_theme(self, name: str) -> None:
        self.theme = THEMES[name]
        self.update()

    def _grid_step(self) -> float:
        step = self.grid_nm if self.grid_nm > 0 else 1000.0
        k, seq, base = 0, (1, 2, 5), step
        while step * self.scale < 9.0:
            k += 1
            step = base * seq[k % 3] * 10 ** (k // 3)
        return step

    def _draw_plate(self, p: QPainter) -> None:
        """Background outlines like CleWin's View|Background settings: wafer circle (+ usable area) and mask rectangle."""
        from ..plate import MM, PlateSettings
        try:
            pl = PlateSettings.from_dict(self.doc.lib.plate)
            pl.validate()
        except Exception:
            return
        if not (pl.show_mask or pl.show_wafer):
            return
        p.save()
        p.setBrush(Qt.NoBrush)
        if pl.show_wafer:
            c = self.to_screen(pl.cx_nm, pl.cy_nm)
            r = pl.wafer_radius_nm() * pl.reduction * self.scale            # wafer drawn at MASK scale: radius * M
            pen = QPen(QColor("#d9a520"), 2)
            p.setPen(pen)
            p.drawEllipse(QPointF(*c), r, r)
            if pl.edge_exclusion_mm > 0:
                ru = pl.usable_radius_mm() * MM * pl.reduction * self.scale
                pen2 = QPen(QColor("#d9a520"), 1, Qt.DashLine)
                p.setPen(pen2)
                p.drawEllipse(QPointF(*c), ru, ru)
            p.setPen(QColor("#d9a520"))
            d = pl.wafer_diameter_mm()
            red = f", drawn x{pl.reduction:g} on the mask" if pl.reduction != 1 else ""
            tx = min(max(c[0] - 40, 6), max(self.width() - 260, 6))
            ty = min(max(c[1] - r + 16, 16), self.height() - 8)
            p.drawText(QPointF(tx, ty), f"Wafer {d:g} mm = {d / 25.4:.2f} inch diameter{red}")
        if pl.show_mask:
            x0, y0, x1, y1 = pl.mask_rect_nm()
            a, b = self.to_screen(x0, y1), self.to_screen(x1, y0)
            p.setPen(QPen(QColor("#4aa3ff"), 2))
            p.drawRect(QRectF(QPointF(*a), QPointF(*b)).normalized())
            p.setPen(QColor("#4aa3ff"))
            w_mm, h_mm = pl.mask_size_mm()
            tx = min(max(a[0] + 6, 6), max(self.width() - 260, 6))
            ty = min(max(a[1] + 16, 16), self.height() - 8)
            p.drawText(QPointF(tx, ty), f"Mask {w_mm:.1f} x {h_mm:.1f} mm = {w_mm / 25.4:.2f} x {h_mm / 25.4:.2f} inch")
        p.restore()

    def _draw_grid(self, p: QPainter) -> None:
        th = self.theme
        x0, y0, x1, y1 = self.visible_rect()
        step = self._grid_step()
        if self.show_grid:
            nx, ny = (x1 - x0) / step, (y1 - y0) / step
            if nx < 3000 and ny < 3000:
                for major in (False, True):
                    p.setPen(QPen(QColor(th.grid_major if major else th.grid_minor), 1))
                    lines = []
                    i0, i1 = math.floor(x0 / step), math.ceil(x1 / step)
                    for i in range(i0, i1 + 1):
                        if (i % 10 == 0) != major:
                            continue
                        sx = self.to_screen(i * step, 0)[0]
                        lines.append((sx, 0, sx, self.height()))
                    j0, j1 = math.floor(y0 / step), math.ceil(y1 / step)
                    for j in range(j0, j1 + 1):
                        if (j % 10 == 0) != major:
                            continue
                        sy = self.to_screen(0, j * step)[1]
                        lines.append((0, sy, self.width(), sy))
                    for a, b, c, d in lines:
                        p.drawLine(QPointF(a, b), QPointF(c, d))
        ox, oy = self.to_screen(0, 0)
        p.setPen(QPen(QColor(th.axis), 1))
        p.drawLine(QPointF(ox, 0), QPointF(ox, self.height()))
        p.drawLine(QPointF(0, oy), QPointF(self.width(), oy))

    def _layer_colors(self, key) -> Optional[QColor]:
        info = self.doc.lib.layers.get(key)
        if info is not None and not info.visible:
            return None
        return QColor(info.color if info is not None else "#4488ff")

    def paintEvent(self, ev) -> None:
        th = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(th.bg))
        self._draw_grid(p)
        self._draw_plate(p)
        vx0, vy0, vx1, vy1 = self.visible_rect()
        p.setTransform(self._xform())

        def in_view(poly: Polygon) -> bool:
            b = poly.bbox
            return not (b[2] < vx0 or b[0] > vx1 or b[3] < vy0 or b[1] > vy1)

        for poly, path in self._refs:                                # instances: dimmed, not editable
            col = self._layer_colors(poly.key)
            if col is None or not in_view(poly):
                continue
            pen = QPen(col, 1)
            pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine)
            fill = QColor(col)
            fill.setAlpha(th.ref_alpha)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawPath(path)

        for poly, path in self._paths.values():
            col = self._layer_colors(poly.key)
            if col is None or not in_view(poly):
                continue
            pen = QPen(col, 1)
            pen.setCosmetic(True)
            fill = QColor(col)
            fill.setAlpha(110)
            p.setPen(pen)
            p.setBrush(fill)
            p.drawPath(path)

        sel_pen = QPen(QColor(th.selection), 2)
        sel_pen.setCosmetic(True)
        sel = [self._paths[id(s)][1] for s in self.doc.selection if id(s) in self._paths]
        p.setBrush(Qt.NoBrush)
        p.setPen(sel_pen)
        for path in sel:
            p.drawPath(path)

        tool = self.tool
        if isinstance(tool, SelectTool):
            if tool.mode == "move" and tool.delta != (0.0, 0.0):    # live move preview
                dash = QPen(QColor(th.preview), 1, Qt.DashLine)
                dash.setCosmetic(True)
                p.setPen(dash)
                p.save()
                p.translate(*tool.delta)
                for path in sel:
                    p.drawPath(path)
                p.restore()
            if tool.band is not None:
                x0, y0, x1, y1 = tool.band
                crossing = x1 < x0
                pen = QPen(QColor(th.preview), 1, Qt.DashLine if crossing else Qt.SolidLine)
                pen.setCosmetic(True)
                c = QColor(th.preview)
                c.setAlpha(30)
                p.setPen(pen)
                p.setBrush(c)
                p.drawRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        else:
            pen = QPen(QColor(th.preview), 1.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for pts, closed in tool.preview():
                poly = QPolygonF([QPointF(float(a), float(b)) for a, b in pts])
                p.drawPolygon(poly) if closed else p.drawPolyline(poly)

        # screen-space overlays: vertex handles and snap crosshair
        p.resetTransform()
        if 0 < len(self.doc.selection) <= 4:
            p.setPen(QPen(QColor(th.selection), 1))
            p.setBrush(QColor(th.bg))
            for s in self.doc.selection:
                if s.num_vertices > 300:
                    continue
                for r in [s.outer] + list(s.holes):
                    for x, y in r:
                        sx, sy = self.to_screen(x, y)
                        p.drawRect(QRectF(sx - 2.5, sy - 2.5, 5, 5))
        if self.underMouse():
            sx, sy = self.to_screen(*self.cursor_nm)
            p.setPen(QPen(QColor(th.preview), 1))
            p.drawLine(QPointF(sx - 6, sy), QPointF(sx + 6, sy))
            p.drawLine(QPointF(sx, sy - 6), QPointF(sx, sy + 6))
        p.end()

    # ---- input -----------------------------------------------------------------------
    def _mods(self, ev) -> Tuple[bool, bool]:
        m = ev.modifiers()
        return bool(m & Qt.ShiftModifier), bool(m & Qt.ControlModifier)

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        pos = ev.position()
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and self._space):
            self._pan = (QPointF(pos), list(self.center))
            self.setCursor(Qt.ClosedHandCursor)
        elif ev.button() == Qt.LeftButton:
            x, y = self.to_scene(pos.x(), pos.y())
            self.tool.press(x, y, *self._mods(ev))
        elif ev.button() == Qt.RightButton:
            self.tool.cancel()
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        if self._pan is not None:
            p0, c0 = self._pan
            self.center = [c0[0] - (pos.x() - p0.x()) / self.scale, c0[1] + (pos.y() - p0.y()) / self.scale]
            self._changed()
            return
        x, y = self.to_scene(pos.x(), pos.y())
        self.cursor_nm = self.snap(x, y)
        self.cursor_moved.emit(*self.cursor_nm)
        self.tool.move(x, y, *self._mods(ev))
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        pos = ev.position()
        if self._pan is not None and ev.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._pan = None
            self.setCursor(Qt.ArrowCursor if isinstance(self.tool, SelectTool) else Qt.CrossCursor)
        elif ev.button() == Qt.LeftButton:
            x, y = self.to_scene(pos.x(), pos.y())
            self.tool.release(x, y, *self._mods(ev))
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            x, y = self.to_scene(ev.position().x(), ev.position().y())
            self.tool.dbl(x, y)
            self.update()

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta().y()
        if d:
            pos = ev.position()
            self.zoom_at(1.25 ** (d / 120.0), pos.x(), pos.y())

    def leaveEvent(self, ev) -> None:
        self.update()

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        k = ev.key()
        if k == Qt.Key_Space and not ev.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.tool.key("enter")
        elif k == Qt.Key_Escape:
            if not self.tool.key("escape") and isinstance(self.tool, SelectTool):
                self.doc.set_selection([])
        elif k == Qt.Key_Backspace:
            self.tool.key("backspace")
        elif k == Qt.Key_Delete:
            self.doc.delete_selection()
        elif k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.doc.selection:
            step = (self.grid_nm if self.grid_nm > 0 else 1000.0) * (10 if ev.modifiers() & Qt.ShiftModifier else 1)
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key_Down: -step, Qt.Key_Up: step}.get(k, 0.0)
            self.doc.move_selection(dx, dy)
        else:
            super().keyPressEvent(ev)
            return
        self.update()

    def keyReleaseEvent(self, ev: QKeyEvent) -> None:
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.ArrowCursor if isinstance(self.tool, SelectTool) else Qt.CrossCursor)
        else:
            super().keyReleaseEvent(ev)


class Ruler(QWidget):
    """Horizontal ('h') or vertical ('v') ruler linked to a Canvas."""
    SIZE = 22

    def __init__(self, canvas: Canvas, orient: str, parent=None):
        super().__init__(parent)
        self.canvas, self.orient = canvas, orient
        if orient == "h":
            self.setFixedHeight(self.SIZE)
        else:
            self.setFixedWidth(self.SIZE)
        canvas.view_changed.connect(self.update)
        canvas.cursor_moved.connect(lambda *_: self.update())

    def paintEvent(self, ev) -> None:
        c, th = self.canvas, self.canvas.theme
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(th.ruler_bg))
        p.setPen(QColor(th.text))
        f = QFont(p.font())
        f.setPointSize(7)
        p.setFont(f)
        horiz = self.orient == "h"
        x0, y0, x1, y1 = c.visible_rect()
        lo, hi = (x0, x1) if horiz else (y0, y1)
        major = nice_step(70.0 / c.scale)
        minor = major / 5.0
        i = math.floor(lo / minor)
        while i * minor <= hi:
            v = i * minor
            is_major = (i % 5 == 0)
            s = c.to_screen(v, v)[0 if horiz else 1]
            L = self.SIZE if is_major else self.SIZE * 0.35
            if minor * c.scale >= 4 or is_major:
                if horiz:
                    p.drawLine(QPointF(s, self.SIZE - L), QPointF(s, self.SIZE))
                else:
                    p.drawLine(QPointF(self.SIZE - L, s), QPointF(self.SIZE, s))
            if is_major:
                txt = fmt_auto(v)
                if horiz:
                    p.drawText(QPointF(s + 3, 10), txt)
                else:
                    p.save()
                    p.translate(10, s - 3)
                    p.rotate(-90)
                    p.drawText(QPointF(0, 0), txt)
                    p.restore()
            i += 1
        cx, cy = c.to_screen(*c.cursor_nm)
        p.setPen(QPen(QColor(th.selection), 1))
        if horiz:
            p.drawLine(QPointF(cx, 0), QPointF(cx, self.SIZE))
        else:
            p.drawLine(QPointF(0, cy), QPointF(self.SIZE, cy))
        p.end()
