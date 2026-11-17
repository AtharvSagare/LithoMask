"""Interactive drawing/selection tools. Qt-free: tools receive scene coordinates in nm and plain bool modifiers,
so they can be driven headlessly. They never edit polygons themselves; they call Document methods."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .. import edit
from ..edit import Document
from ..geometry import Polygon, bbox_of, circle, ellipse, path, rectangle, ring


@dataclass
class ToolOptions:
    wire_width: float = 1000.0      # nm
    wire_end: str = "flat"          # flat | square | round
    wire_join: str = "miter"        # miter | round | bevel
    tol: float = 1.0                # max sagitta (nm) for circles/ellipses/rings/round wires


class ToolContext:
    """What tools need from the canvas / main window. Callbacks default to headless-friendly no-ops."""

    def __init__(self, doc: Document, options: ToolOptions):
        self.doc = doc
        self.options = options
        self.px_per_nm = 0.05
        self.snap: Callable[[float, float], Tuple[float, float]] = lambda x, y: (x, y)
        self.ask_ring_inner: Callable[[float], Optional[float]] = lambda r_out: None
        self.ask_text: Callable[[], Optional[dict]] = lambda: None
        self.text_rings: Callable[[str, float, str, bool], List[np.ndarray]] = lambda t, h, f, b: []
        self.message: Callable[[str], None] = lambda s: None


Preview = List[Tuple[np.ndarray, bool]]   # (Nx2 points in nm, closed?)


class Tool:
    name = "tool"
    label = "Tool"
    cursor = "cross"

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    def press(self, x, y, shift=False, ctrl=False): ...
    def move(self, x, y, shift=False, ctrl=False): ...
    def release(self, x, y, shift=False, ctrl=False): ...
    def dbl(self, x, y): ...
    def key(self, name: str) -> bool:           # 'enter' | 'escape' | 'backspace'
        return False
    def cancel(self): ...
    def preview(self) -> Preview:
        return []


class SelectTool(Tool):
    name, label, cursor = "select", "Select", "arrow"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.mode: Optional[str] = None
        self.start = (0.0, 0.0)
        self.delta = (0.0, 0.0)
        self.band: Optional[Tuple[float, float, float, float]] = None
        self._add = False
        self._b0 = (0.0, 0.0)

    def press(self, x, y, shift=False, ctrl=False):
        doc = self.ctx.doc
        tol = 4.0 / self.ctx.px_per_nm
        hit = edit.pick_point(doc.cell.polygons, x, y, tol, doc.selectable)
        self.start, self.delta, self.mode, self.band = (x, y), (0.0, 0.0), None, None
        if hit is not None:
            selected = {id(p) for p in doc.selection}
            if shift or ctrl:                                   # toggle membership, no drag
                if id(hit) in selected:
                    doc.set_selection([p for p in doc.selection if p is not hit])
                else:
                    doc.set_selection(doc.selection + [hit])
                return
            if id(hit) not in selected:
                doc.set_selection([hit])
            self.mode = "move"
            bb = bbox_of(doc.selection)
            self._b0 = (bb[0], bb[1])
        else:
            self.mode, self._add = "band", (shift or ctrl)
            self.band = (x, y, x, y)
            if not self._add:
                doc.set_selection([])

    def move(self, x, y, shift=False, ctrl=False):
        if self.mode == "move":
            bx, by = self._b0
            nx, ny = self.ctx.snap(bx + x - self.start[0], by + y - self.start[1])   # keep the bbox corner on the grid
            self.delta = (nx - bx, ny - by)
        elif self.mode == "band":
            self.band = (self.start[0], self.start[1], x, y)

    def release(self, x, y, shift=False, ctrl=False):
        doc = self.ctx.doc
        if self.mode == "move" and self.delta != (0.0, 0.0):
            doc.move_selection(*self.delta)
        elif self.mode == "band" and self.band is not None:
            x0, y0, x1, y1 = self.band
            if abs(x1 - x0) * self.ctx.px_per_nm > 3 or abs(y1 - y0) * self.ctx.px_per_nm > 3:
                crossing = x1 < x0                              # right-to-left = crossing, left-to-right = window
                got = edit.pick_rect(doc.cell.polygons, self.band, crossing, doc.selectable)
                if self._add:
                    have = {id(p) for p in doc.selection}
                    got = doc.selection + [p for p in got if id(p) not in have]
                doc.set_selection(got)
        self.mode, self.band, self.delta = None, None, (0.0, 0.0)

    def cancel(self):
        self.mode, self.band, self.delta = None, None, (0.0, 0.0)


class _DragShape(Tool):
    """Press-drag-release shapes. Subclasses implement build(p0, p1) -> list[Polygon]."""
    label_draw = "Draw"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.p0: Optional[Tuple[float, float]] = None
        self.p1: Optional[Tuple[float, float]] = None

    def build(self, p0, p1) -> List[Polygon]:
        raise NotImplementedError

    def press(self, x, y, shift=False, ctrl=False):
        self.p0 = self.p1 = self.ctx.snap(x, y)

    def move(self, x, y, shift=False, ctrl=False):
        if self.p0 is not None:
            self.p1 = self.ctx.snap(x, y)

    def finish(self, p0, p1):
        polys = self.build(p0, p1)
        if polys:
            self.ctx.doc.add_polygons(polys, self.label_draw)

    def release(self, x, y, shift=False, ctrl=False):
        if self.p0 is None:
            return
        p0, p1 = self.p0, self.ctx.snap(x, y)
        self.p0 = self.p1 = None
        self.finish(p0, p1)

    def cancel(self):
        self.p0 = self.p1 = None

    def key(self, name):
        if name == "escape":
            self.cancel()
            return True
        return False

    def preview(self):
        if self.p0 is None:
            return []
        return [(p.outer, True) for p in self.build(self.p0, self.p1)]


class RectTool(_DragShape):
    name, label = "rect", "Rectangle"
    label_draw = "Rectangle"

    def build(self, p0, p1):
        if p0[0] == p1[0] or p0[1] == p1[1]:
            return []
        L, D = self.ctx.doc.current_layer
        return [rectangle(p0[0], p0[1], p1[0], p1[1], L, D)]


class EllipseTool(_DragShape):
    name, label = "ellipse", "Ellipse"
    label_draw = "Ellipse"

    def build(self, p0, p1):
        rx, ry = abs(p1[0] - p0[0]) / 2, abs(p1[1] - p0[1]) / 2
        if rx <= 0 or ry <= 0:
            return []
        L, D = self.ctx.doc.current_layer
        return [ellipse((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, rx, ry, self.ctx.options.tol, 0.0, L, D)]


class CircleTool(_DragShape):
    name, label = "circle", "Circle"
    label_draw = "Circle"

    def build(self, p0, p1):
        r = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if r <= 0:
            return []
        L, D = self.ctx.doc.current_layer
        return [circle(p0[0], p0[1], r, self.ctx.options.tol, L, D)]


class RingTool(_DragShape):
    name, label = "ring", "Ring"
    label_draw = "Ring"

    def build(self, p0, p1, r_in: Optional[float] = None):
        r = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if r <= 0:
            return []
        L, D = self.ctx.doc.current_layer
        if r_in is None:                                   # preview: show the outer circle only
            return [circle(p0[0], p0[1], r, self.ctx.options.tol, L, D)]
        return [ring(p0[0], p0[1], r_in, r, self.ctx.options.tol, L, D)]

    def finish(self, p0, p1):
        r = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if r <= 0:
            return
        r_in = self.ctx.ask_ring_inner(r)
        if r_in is None or not 0 < r_in < r:
            self.ctx.message("Ring cancelled (inner radius must be between 0 and the outer radius)")
            return
        self.ctx.doc.add_polygons(self.build(p0, p1, r_in), self.label_draw)


def _ortho(prev, p):
    """Constrain p to the nearest multiple of 45 degrees about prev (Shift)."""
    dx, dy = p[0] - prev[0], p[1] - prev[1]
    if dx == 0 and dy == 0:
        return p
    a = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    c, s = math.cos(a), math.sin(a)
    L = dx * c + dy * s
    return (prev[0] + L * c, prev[1] + L * s)


class PolygonTool(Tool):
    name, label = "polygon", "Polygon"
    min_pts = 3
    closed = True

    def __init__(self, ctx):
        super().__init__(ctx)
        self.pts: List[Tuple[float, float]] = []
        self.cur: Optional[Tuple[float, float]] = None

    def _pt(self, x, y, shift):
        p = self.ctx.snap(x, y)
        return _ortho(self.pts[-1], p) if (shift and self.pts) else p

    def press(self, x, y, shift=False, ctrl=False):
        p = self._pt(x, y, shift)
        if not self.pts or p != self.pts[-1]:
            self.pts.append(p)
        self.cur = p

    def move(self, x, y, shift=False, ctrl=False):
        self.cur = self._pt(x, y, shift)

    def dbl(self, x, y):
        self.finish()

    def key(self, name):
        if name == "enter":
            self.finish()
        elif name == "escape":
            self.cancel()
        elif name == "backspace":
            if self.pts:
                self.pts.pop()
        else:
            return False
        return True

    def cancel(self):
        self.pts, self.cur = [], None

    def build(self, pts) -> List[Polygon]:
        L, D = self.ctx.doc.current_layer
        p = Polygon(np.array(pts), [], L, D)
        return [p] if p.area > 0 else []

    def finish(self):
        if len(self.pts) >= self.min_pts:
            polys = self.build(self.pts)
            if polys:
                self.ctx.doc.add_polygons(polys, self.label)
            else:
                self.ctx.message("Degenerate shape ignored")
        self.cancel()

    def preview(self):
        if not self.pts:
            return []
        pts = self.pts + ([self.cur] if self.cur else [])
        if self.closed and len(pts) >= 3:
            pts = pts + [self.pts[0]]
        return [(np.array(pts, float), False)]


class WireTool(PolygonTool):
    name, label = "wire", "Wire"
    min_pts = 2
    closed = False

    def build(self, pts):
        o = self.ctx.options
        L, D = self.ctx.doc.current_layer
        uniq = [pts[0]] + [q for a, q in zip(pts, pts[1:]) if q != a]
        return path(uniq, o.wire_width, o.wire_end, o.wire_join, o.tol, L, D) if len(uniq) >= 2 else []

    def preview(self):
        pts = self.pts + ([self.cur] if self.cur else [])
        out: Preview = []
        if len(pts) >= 2:
            try:
                out = [(p.outer, True) for p in self.build(pts)]
            except Exception:
                out = []
        if pts:
            out.append((np.array(pts, float), False))
        return out


class TextTool(Tool):
    name, label = "text", "Text"

    def press(self, x, y, shift=False, ctrl=False):
        info = self.ctx.ask_text()
        if not info:
            return
        px, py = self.ctx.snap(x, y)
        rings = self.ctx.text_rings(info["text"], info["height"], info.get("family", ""), info.get("bold", False))
        L, D = self.ctx.doc.current_layer
        polys = edit.rings_to_polygons([np.asarray(r) + (px, py) for r in rings], L, D)
        if polys:
            self.ctx.doc.add_polygons(polys, "Text")
        else:
            self.ctx.message("Text produced no outlines")


TOOLS: Dict[str, type] = {c.name: c for c in
                          (SelectTool, RectTool, PolygonTool, CircleTool, EllipseTool, RingTool, WireTool, TextTool)}
