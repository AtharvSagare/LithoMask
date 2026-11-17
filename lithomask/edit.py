"""lithomask.edit - headless editing layer used by the GUI (no Qt imports here).

Everything the GUI does to a design goes through this module, so it can be tested without a display:

* Command / UndoStack  - command pattern with macros and a "clean" marker for the dirty flag.
* Document             - Library + active cell + selection + undo stack + file I/O. The GUI only calls its methods.
* Pure editing ops     - moved/rotated/mirrored/scaled/aligned/distributed/array/boolean/offset on lists of Polygon.
* Picking              - pick_point / pick_rect (hit testing in nm, topmost-last draw order).

Conventions: nm everywhere, angles in degrees CCW, Polygon objects are treated as IMMUTABLE once in a cell; every edit
replaces polygons with new objects. Polygons are identified by id() (Polygon is a numpy-holding dataclass, so == and `in`
on lists of Polygon must not be used).
"""
from __future__ import annotations

import math
import os
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from shapely.geometry import Point as _Pt
from shapely.geometry import Polygon as _SPoly
from shapely.geometry import box as _box

from .geometry import Polygon, bbox_of, boolean, rectangle  # rectangle re-exported for callers/tests
from .geometry import offset as _offset
from .layout import Cell, LayerInfo, Library, Reference

LayerKey = Tuple[int, int]
PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#2cc3c3",
           "#f032e6", "#9bc20a", "#e8a0a0", "#008080", "#b58be0", "#9a6324"]


# ======================================================================================
# Commands and undo stack
# ======================================================================================
class Command:
    text = "edit"

    def redo(self) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        raise NotImplementedError

    def selection_after(self, direction: str) -> Optional[List[Polygon]]:
        """Polygons to select after 'redo' / 'undo' of this command; None = no opinion (selection is only pruned)."""
        return None


class MacroCommand(Command):
    def __init__(self, text: str, cmds: Optional[Sequence[Command]] = None):
        self.text = text
        self.cmds: List[Command] = list(cmds or [])

    def redo(self) -> None:
        for c in self.cmds:
            c.redo()

    def undo(self) -> None:
        for c in reversed(self.cmds):
            c.undo()

    def selection_after(self, direction: str):
        reps = [c for c in self.cmds if isinstance(c, ReplacePolys)]
        if not reps:
            return None
        return reps[-1].selection_after("redo") if direction == "redo" else reps[0].selection_after("undo")


class ReplacePolys(Command):
    """Remove `old` polygons (by identity) from `cell` and insert `new` at the position of the first removed one
    (appended when `old` is empty). Covers add, delete, move, transform, boolean, layer change, vertex edit."""

    def __init__(self, cell: Cell, old: Sequence[Polygon], new: Sequence[Polygon], text: str = "Edit"):
        self.cell, self.old, self.new, self.text = cell, list(old), list(new), text
        self._idx: List[int] = []
        self._old_sorted: List[Polygon] = []

    def redo(self) -> None:
        polys = self.cell.polygons
        ids = {id(p) for p in self.old}
        self._idx = [i for i, p in enumerate(polys) if id(p) in ids]
        self._old_sorted = [polys[i] for i in self._idx]
        at = self._idx[0] if self._idx else len(polys)
        rest = [p for p in polys if id(p) not in ids]
        rest[at:at] = self.new
        polys[:] = rest

    def undo(self) -> None:
        polys = self.cell.polygons
        nid = {id(p) for p in self.new}
        rest = [p for p in polys if id(p) not in nid]
        for i, p in zip(self._idx, self._old_sorted):
            rest.insert(i, p)
        polys[:] = rest

    def selection_after(self, direction: str):
        return list(self.new) if direction == "redo" else list(self._old_sorted or self.old)


class AddReference(Command):
    def __init__(self, cell: Cell, ref: Reference, text: str = "Place instance"):
        self.cell, self.ref, self.text = cell, ref, text

    def redo(self) -> None:
        self.cell.references.append(self.ref)

    def undo(self) -> None:
        self.cell.references[:] = [r for r in self.cell.references if r is not self.ref]


class RemoveReference(Command):
    def __init__(self, cell: Cell, ref: Reference, text: str = "Remove instance"):
        self.cell, self.ref, self.text = cell, ref, text
        self._i = 0

    def redo(self) -> None:
        self._i = next(i for i, r in enumerate(self.cell.references) if r is self.ref)
        del self.cell.references[self._i]

    def undo(self) -> None:
        self.cell.references.insert(self._i, self.ref)


class AddCell(Command):
    def __init__(self, lib: Library, cell: Cell, text: str = "New cell"):
        self.lib, self.cell, self.text = lib, cell, text

    def redo(self) -> None:
        self.lib.cells[self.cell.name] = self.cell

    def undo(self) -> None:
        self.lib.cells.pop(self.cell.name, None)


class RemoveCell(Command):
    def __init__(self, lib: Library, cell: Cell, text: str = "Delete cell"):
        self.lib, self.cell, self.text = lib, cell, text

    def redo(self) -> None:
        self.lib.cells.pop(self.cell.name, None)

    def undo(self) -> None:
        self.lib.cells[self.cell.name] = self.cell


class RenameCell(Command):
    def __init__(self, lib: Library, cell: Cell, new_name: str, text: str = "Rename cell"):
        self.lib, self.cell, self.old_name, self.new_name, self.text = lib, cell, cell.name, new_name, text

    def _set(self, frm: str, to: str) -> None:
        items = [((to if k == frm else k), v) for k, v in self.lib.cells.items()]
        self.lib.cells.clear()
        self.lib.cells.update(items)
        self.cell.name = to

    def redo(self) -> None:
        self._set(self.old_name, self.new_name)

    def undo(self) -> None:
        self._set(self.new_name, self.old_name)


class SetLayerInfo(Command):
    def __init__(self, lib: Library, key: LayerKey, new: LayerInfo, text: str = "Edit layer"):
        self.lib, self.key, self.new, self.text = lib, key, new, text
        self.old = lib.layers.get(key)

    def redo(self) -> None:
        self.lib.layers[self.key] = self.new

    def undo(self) -> None:
        if self.old is None:
            self.lib.layers.pop(self.key, None)
        else:
            self.lib.layers[self.key] = self.old


class SetPlate(Command):
    """Change the mask-plate / wafer settings stored in the project (lithomask.plate.PlateSettings.to_dict())."""
    def __init__(self, lib: Library, new: dict, text: str = "Mask / wafer settings"):
        self.lib, self.new, self.text = lib, dict(new), text
        self.old = dict(lib.plate)

    def redo(self) -> None:
        self.lib.plate = dict(self.new)

    def undo(self) -> None:
        self.lib.plate = dict(self.old)


class UndoStack:
    """Linear undo/redo with macros. listeners(cmd, direction) are called after every change ('redo' | 'undo')."""

    def __init__(self, limit: int = 1000):
        self.limit = limit
        self._cmds: List[Command] = []
        self._idx = 0
        self._clean: Optional[int] = 0
        self._macro: Optional[MacroCommand] = None
        self._depth = 0
        self.listeners: List[Callable[[Optional[Command], str], None]] = []

    # -- state
    @property
    def can_undo(self) -> bool:
        return self._idx > 0

    @property
    def can_redo(self) -> bool:
        return self._idx < len(self._cmds)

    @property
    def undo_text(self) -> str:
        return self._cmds[self._idx - 1].text if self.can_undo else ""

    @property
    def redo_text(self) -> str:
        return self._cmds[self._idx].text if self.can_redo else ""

    @property
    def is_clean(self) -> bool:
        return self._clean == self._idx

    def set_clean(self) -> None:
        self._clean = self._idx
        self._notify(None, "clean")

    def _notify(self, cmd, direction) -> None:
        for fn in list(self.listeners):
            fn(cmd, direction)

    # -- operations
    def push(self, cmd: Command) -> None:
        cmd.redo()
        if self._macro is not None:
            self._macro.cmds.append(cmd)
            return
        self._record(cmd)
        self._notify(cmd, "redo")

    def _record(self, cmd: Command) -> None:
        del self._cmds[self._idx:]
        if self._clean is not None and self._clean > self._idx:
            self._clean = None  # the clean state was undone-then-overwritten: unreachable
        self._cmds.append(cmd)
        self._idx += 1
        if len(self._cmds) > self.limit:
            del self._cmds[0]
            self._idx -= 1
            if self._clean is not None:
                self._clean -= 1
                if self._clean < 0:
                    self._clean = None

    def begin_macro(self, text: str) -> None:
        self._depth += 1
        if self._depth == 1:
            self._macro = MacroCommand(text)

    def end_macro(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            m, self._macro = self._macro, None
            if m is not None and m.cmds:
                self._record(m)
                self._notify(m, "redo")

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        cmd = self._cmds[self._idx - 1]
        cmd.undo()
        self._idx -= 1
        self._notify(cmd, "undo")
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        cmd = self._cmds[self._idx]
        cmd.redo()
        self._idx += 1
        self._notify(cmd, "redo")
        return True


# ======================================================================================
# Pure editing operations (lists of Polygon in, new list out; inputs never mutated)
# ======================================================================================
def center_of(polys: Sequence[Polygon]) -> Tuple[float, float]:
    x0, y0, x1, y1 = bbox_of(polys)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def moved(polys, dx: float, dy: float) -> List[Polygon]:
    return [p.translate(dx, dy) for p in polys]


def rotated(polys, angle: float, center=None) -> List[Polygon]:
    c = center if center is not None else center_of(polys)
    return [p.rotate(angle, c) for p in polys]


def mirrored(polys, axis: str = "x", center=None) -> List[Polygon]:
    """axis='x': reflect y about the horizontal line y=cy (flip up/down); axis='y': reflect x about x=cx (flip left/right)."""
    cx, cy = center if center is not None else center_of(polys)
    return [p.mirror(axis, cy if axis == "x" else cx) for p in polys]


def scaled(polys, sx: float, sy: Optional[float] = None, center=None) -> List[Polygon]:
    sy = sx if sy is None else sy
    if sx <= 0 or sy <= 0:
        raise ValueError("scale factors must be > 0 (use mirror to flip)")
    c = center if center is not None else center_of(polys)
    return [p.scale(sx, sy, c) for p in polys]


def set_bbox(polys, x=None, y=None, w=None, h=None) -> List[Polygon]:
    """Move/resize the selection so its bounding box becomes (x, y, w, h); None keeps that value. Scales about the lower-left."""
    x0, y0, x1, y1 = bbox_of(polys)
    W, H = x1 - x0, y1 - y0
    if (w is not None and w <= 0) or (h is not None and h <= 0):
        raise ValueError("width and height must be > 0")
    sx = (w / W) if (w is not None and W > 0) else 1.0
    sy = (h / H) if (h is not None and H > 0) else 1.0
    nx = x0 if x is None else x
    ny = y0 if y is None else y
    return [p.scale(sx, sy, (x0, y0)).translate(nx - x0, ny - y0) for p in polys]


def aligned(polys, mode: str) -> List[Polygon]:
    """mode: left | right | hcenter | top | bottom | vcenter, relative to the selection bounding box."""
    X0, Y0, X1, Y1 = bbox_of(polys)
    out = []
    for p in polys:
        x0, y0, x1, y1 = p.bbox
        dx = dy = 0.0
        if mode == "left":
            dx = X0 - x0
        elif mode == "right":
            dx = X1 - x1
        elif mode == "hcenter":
            dx = (X0 + X1) / 2 - (x0 + x1) / 2
        elif mode == "bottom":
            dy = Y0 - y0
        elif mode == "top":
            dy = Y1 - y1
        elif mode == "vcenter":
            dy = (Y0 + Y1) / 2 - (y0 + y1) / 2
        else:
            raise ValueError(f"unknown align mode {mode!r}")
        out.append(p.translate(dx, dy))
    return out


def distributed(polys, axis: str = "x", mode: str = "gaps") -> List[Polygon]:
    """Distribute along axis ('x'|'y'). mode 'gaps': equal gaps between bounding boxes; 'centers': equal centre pitch.
    The two outermost objects stay put; needs >= 3 objects (otherwise returns copies)."""
    n = len(polys)
    if n < 3:
        return [p.copy() for p in polys]
    i0 = 0 if axis == "x" else 1
    lo = [p.bbox[i0] for p in polys]
    hi = [p.bbox[i0 + 2] for p in polys]
    order = sorted(range(n), key=lambda i: lo[i] + hi[i])
    shifts = [0.0] * n
    if mode == "gaps":
        span = hi[order[-1]] - lo[order[0]] - sum(hi[i] - lo[i] for i in order)
        gap, pos = span / (n - 1), lo[order[0]]
        for i in order:
            shifts[i] = pos - lo[i]
            pos += (hi[i] - lo[i]) + gap
    elif mode == "centers":
        c0 = (lo[order[0]] + hi[order[0]]) / 2
        c1 = (lo[order[-1]] + hi[order[-1]]) / 2
        for k, i in enumerate(order):
            shifts[i] = c0 + (c1 - c0) * k / (n - 1) - (lo[i] + hi[i]) / 2
    else:
        raise ValueError(f"unknown distribute mode {mode!r}")
    return [p.translate(s, 0.0) if axis == "x" else p.translate(0.0, s) for p, s in zip(polys, shifts)]


def array_copies(polys, rows: int, cols: int, dx: float, dy: float) -> List[Polygon]:
    """Copies for a rows x cols array; element (0,0) is the original and is NOT included. Pitches in nm (may be negative)."""
    if rows < 1 or cols < 1:
        raise ValueError("rows and cols must be >= 1")
    out: List[Polygon] = []
    for r in range(rows):
        for c in range(cols):
            if r == 0 and c == 0:
                continue
            out.extend(p.translate(c * dx, r * dy) for p in polys)
    return out


def boolean_op(polys, op: str) -> List[Polygon]:
    """'or': union of all. 'and' / 'not' / 'xor': first polygon is A, the union of the rest is B. Result takes A's layer."""
    if op == "or":
        return boolean(list(polys), [], "or")
    if len(polys) < 2:
        raise ValueError("need at least two objects")
    return boolean([polys[0]], list(polys[1:]), op)


def offset_polys(polys, delta: float, join: str = "miter") -> List[Polygon]:
    return _offset(list(polys), delta, join=join)


def with_layer(polys, layer: int, datatype: int) -> List[Polygon]:
    out = []
    for p in polys:
        q = p.copy()
        q.layer, q.datatype = layer, datatype
        out.append(q)
    return out


def polygon_with_vertex(p: Polygon, ring: int, idx: int, x: float, y: float) -> Polygon:
    """Copy of p with vertex idx of ring (0 = outer, 1.. = holes) moved to (x, y)."""
    q = p.copy()
    r = (q.outer if ring == 0 else q.holes[ring - 1]).copy()
    r[idx] = (x, y)
    if ring == 0:
        return Polygon(r, q.holes, q.layer, q.datatype)
    holes = list(q.holes)
    holes[ring - 1] = r
    return Polygon(q.outer, holes, q.layer, q.datatype)


def rings_to_polygons(rings: Iterable[Sequence[Sequence[float]]], layer: int = 1, datatype: int = 0) -> List[Polygon]:
    """Even-odd combination of closed rings (glyph outlines): nested rings become holes."""
    acc = None
    for r in rings:
        a = np.asarray(r, float).reshape(-1, 2)
        if len(a) < 3:
            continue
        sp = shapely.make_valid(_SPoly(a))
        acc = sp if acc is None else acc.symmetric_difference(sp)
    return Polygon.from_shapely(acc, layer, datatype) if acc is not None else []


# ======================================================================================
# Picking
# ======================================================================================
def pick_point(polys: Sequence[Polygon], x: float, y: float, tol: float,
               selectable: Optional[Callable[[Polygon], bool]] = None) -> Optional[Polygon]:
    """Topmost (last in list) polygon whose material contains (x, y) or lies within `tol` nm of it."""
    pt = _Pt(x, y)
    for p in reversed(polys):
        if selectable is not None and not selectable(p):
            continue
        x0, y0, x1, y1 = p.bbox
        if x < x0 - tol or x > x1 + tol or y < y0 - tol or y > y1 + tol:
            continue
        if p.to_shapely().distance(pt) <= tol:
            return p
    return None


def pick_rect(polys: Sequence[Polygon], rect: Tuple[float, float, float, float], crossing: bool,
              selectable: Optional[Callable[[Polygon], bool]] = None) -> List[Polygon]:
    """Window select (crossing=False: fully inside) or crossing select (touching/intersecting). Draw order preserved."""
    xa, xb = sorted((rect[0], rect[2]))
    ya, yb = sorted((rect[1], rect[3]))
    box = _box(xa, ya, xb, yb)
    out = []
    for p in polys:
        if selectable is not None and not selectable(p):
            continue
        x0, y0, x1, y1 = p.bbox
        if crossing:
            if x1 < xa or x0 > xb or y1 < ya or y0 > yb:
                continue
            if p.to_shapely().intersects(box):
                out.append(p)
        elif x0 >= xa and x1 <= xb and y0 >= ya and y1 <= yb:
            out.append(p)
    return out


# ======================================================================================
# Document
# ======================================================================================
_CLIPBOARD: List[Polygon] = []


class Document:
    """A design being edited. Listeners get kind in {'content','selection','layers','cells','file'}."""

    def __init__(self, lib: Optional[Library] = None, path: Optional[str] = None):
        self.lib = lib if lib is not None else Library()
        if not self.lib.cells:
            self.lib.new_cell("TOP")
        self.path = path
        tops = self.lib.top_cells()
        self._cell: Cell = tops[0] if tops else next(iter(self.lib.cells.values()))
        self.stack = UndoStack()
        self.stack.listeners.append(self._on_stack)
        self.selection: List[Polygon] = []
        self.current_layer: LayerKey = (1, 0)
        self.listeners: List[Callable[[str], None]] = []
        self.ensure_layers()

    # ---- notification / basics -------------------------------------------------
    def _emit(self, kind: str) -> None:
        for fn in list(self.listeners):
            fn(kind)

    @property
    def cell(self) -> Cell:
        """Active cell. Tracked by object, so renames follow; falls back to a top cell if it was removed (undo/delete)."""
        if self.lib.cells.get(self._cell.name) is not self._cell:
            tops = self.lib.top_cells()
            self._cell = tops[0] if tops else next(iter(self.lib.cells.values()))
        return self._cell

    @property
    def cell_name(self) -> str:
        return self.cell.name

    @property
    def is_dirty(self) -> bool:
        return not self.stack.is_clean

    def _on_stack(self, cmd, direction) -> None:
        if direction == "clean":
            self._emit("file")
            return
        sel = cmd.selection_after(direction) if cmd is not None else None
        if sel is not None:
            self.selection = list(sel)
        self._prune_selection()
        self.ensure_layers()
        self._emit("content")
        self._emit("selection")

    def _prune_selection(self) -> None:
        ids = {id(p) for p in self.cell.polygons}
        self.selection = [p for p in self.selection if id(p) in ids]

    def execute(self, cmd: Command) -> None:
        self.stack.push(cmd)

    @contextmanager
    def macro(self, text: str):
        self.stack.begin_macro(text)
        try:
            yield
        finally:
            self.stack.end_macro()

    def undo(self) -> bool:
        return self.stack.undo()

    def redo(self) -> bool:
        return self.stack.redo()

    # ---- layers ----------------------------------------------------------------
    def ensure_layers(self, polys: Optional[Iterable[Polygon]] = None) -> None:
        if polys is None:
            polys = [p for c in self.lib.cells.values() for p in c.polygons]
        changed = False
        for p in polys:
            if p.key not in self.lib.layers:
                n = p.layer + 3 * p.datatype
                self.lib.layers[p.key] = LayerInfo(f"{p.layer}/{p.datatype}", PALETTE[n % len(PALETTE)])
                changed = True
        if self.current_layer not in self.lib.layers:
            self.lib.layers[self.current_layer] = LayerInfo(f"{self.current_layer[0]}/{self.current_layer[1]}",
                                                            PALETTE[(self.current_layer[0] + 3 * self.current_layer[1]) % len(PALETTE)])
            changed = True
        if changed:
            self._emit("layers")

    def layer_info(self, key: LayerKey) -> LayerInfo:
        self.ensure_layers([Polygon([(0, 0), (1, 0), (0, 1)], [], key[0], key[1])])
        return self.lib.layers[key]

    def selectable(self, p: Polygon) -> bool:
        i = self.lib.layers.get(p.key)
        return i is None or (i.visible and not i.locked)

    def set_current_layer(self, key: LayerKey) -> None:
        self.current_layer = key
        self.ensure_layers()
        self._emit("layers")

    def get_plate(self):
        """PlateSettings of this project (defaults if never set)."""
        from .plate import PlateSettings
        return PlateSettings.from_dict(self.lib.plate)

    def set_plate(self, plate) -> None:
        """Undoable; plate is a lithomask.plate.PlateSettings (validated first)."""
        plate.validate()
        self.execute(SetPlate(self.lib, plate.to_dict()))
        self._emit("content")

    def set_layer_info(self, key: LayerKey, **changes) -> None:
        old = self.layer_info(key)
        new = LayerInfo(old.name, old.color, old.visible, old.locked)
        for k, v in changes.items():
            setattr(new, k, v)
        self.execute(SetLayerInfo(self.lib, key, new, "Edit layer"))
        if not new.visible or new.locked:
            ids = {id(p) for p in self.cell.polygons if p.key == key}
            self.selection = [p for p in self.selection if id(p) not in ids]
            self._emit("selection")
        self._emit("layers")

    # ---- selection -------------------------------------------------------------
    def set_selection(self, polys: Iterable[Polygon]) -> None:
        self.selection = list(polys)
        self._prune_selection()
        self._emit("selection")

    def select_all(self) -> None:
        self.set_selection([p for p in self.cell.polygons if self.selectable(p)])

    # ---- editing ---------------------------------------------------------------
    def add_polygons(self, polys: Iterable[Polygon], text: str = "Draw") -> List[Polygon]:
        polys = list(polys)
        if polys:
            self.ensure_layers(polys)
            self.execute(ReplacePolys(self.cell, [], polys, text))
        return polys

    def apply_to_selection(self, fn: Callable[[List[Polygon]], List[Polygon]], text: str) -> int:
        """Replace the selection by fn(selection); the result becomes the new selection. Returns result count."""
        sel = list(self.selection)
        if not sel:
            return 0
        new = fn(sel)
        self.ensure_layers(new)
        self.execute(ReplacePolys(self.cell, sel, new, text))
        return len(new)

    def delete_selection(self) -> None:
        self.apply_to_selection(lambda s: [], "Delete")

    def move_selection(self, dx: float, dy: float) -> None:
        if dx or dy:
            self.apply_to_selection(lambda s: moved(s, dx, dy), "Move")

    def set_layer_of_selection(self, layer: int, datatype: int) -> None:
        self.apply_to_selection(lambda s: with_layer(s, layer, datatype), "Change layer")

    def replace(self, old: Sequence[Polygon], new: Sequence[Polygon], text: str = "Edit") -> None:
        self.ensure_layers(new)
        self.execute(ReplacePolys(self.cell, old, new, text))

    def duplicate_selection(self, dx: float, dy: float) -> None:
        sel = list(self.selection)
        if sel:
            self.add_polygons(moved(sel, dx, dy), "Duplicate")

    def copy_selection(self) -> int:
        _CLIPBOARD[:] = [p.copy() for p in self.selection]
        return len(_CLIPBOARD)

    def paste(self, dx: float = 0.0, dy: float = 0.0) -> int:
        if not _CLIPBOARD:
            return 0
        return len(self.add_polygons(moved(_CLIPBOARD, dx, dy), "Paste"))

    def array_selection(self, rows: int, cols: int, dx: float, dy: float, as_reference: bool = False) -> int:
        """Array the selection. as_reference=False: polygon copies. True: the selection moves into a new cell that is
        placed once as a GDSII-style array reference (compact, hierarchical)."""
        sel = list(self.selection)
        if not sel or rows < 1 or cols < 1:
            return 0
        if not as_reference:
            copies = array_copies(sel, rows, cols, dx, dy)
            with self.macro("Array"):
                self.execute(ReplacePolys(self.cell, [], copies, "Array"))
            self.set_selection(sel + copies)
            return len(copies)
        name = self.unique_cell_name("ARRAY")
        sub = Cell(name, [p.copy() for p in sel])
        with self.macro("Array (reference)"):
            self.execute(AddCell(self.lib, sub))
            self.execute(ReplacePolys(self.cell, sel, [], "Array"))
            self.execute(AddReference(self.cell, Reference(sub, (0.0, 0.0), columns=cols, rows=rows, spacing=(dx, dy))))
        self._emit("cells")
        return rows * cols

    # ---- cells -----------------------------------------------------------------
    def unique_cell_name(self, base: str = "CELL") -> str:
        if base not in self.lib.cells:
            return base
        i = 1
        while f"{base}_{i}" in self.lib.cells:
            i += 1
        return f"{base}_{i}"

    def set_cell(self, name: str) -> None:
        if name not in self.lib.cells:
            raise KeyError(name)
        self._cell = self.lib.cells[name]
        self.selection = []
        self._emit("cells")
        self._emit("content")
        self._emit("selection")

    def new_cell(self, name: Optional[str] = None, activate: bool = True) -> Cell:
        c = Cell(self.unique_cell_name(name or "CELL"))
        self.execute(AddCell(self.lib, c))
        if activate:
            self.set_cell(c.name)
        self._emit("cells")
        return c

    def referenced_by(self, name: str) -> List[str]:
        return [c.name for c in self.lib.cells.values() if any(r.cell.name == name for r in c.references)]

    def delete_cell(self, name: str) -> None:
        if len(self.lib.cells) <= 1:
            raise ValueError("cannot delete the only cell")
        users = self.referenced_by(name)
        if users:
            raise ValueError(f"cell '{name}' is used by: {', '.join(users)}")
        c = self.lib.cells[name]
        if self.cell_name == name:
            self.set_cell(next(n for n in self.lib.cells if n != name))
        self.execute(RemoveCell(self.lib, c))
        self._emit("cells")

    def rename_cell(self, old: str, new: str) -> None:
        if not new or new == old:
            return
        if new in self.lib.cells:
            raise ValueError(f"cell '{new}' already exists")
        self.execute(RenameCell(self.lib, self.lib.cells[old], new))
        self._emit("cells")

    def _reaches(self, cell: Cell, name: str, seen=None) -> bool:
        seen = seen or set()
        if cell.name == name:
            return True
        if cell.name in seen:
            return False
        seen.add(cell.name)
        return any(self._reaches(r.cell, name, seen) for r in cell.references)

    def place_reference(self, target: str, origin=(0.0, 0.0), rotation: float = 0.0, x_reflection: bool = False,
                        magnification: float = 1.0, columns: int = 1, rows: int = 1, spacing=(0.0, 0.0)) -> Reference:
        t = self.lib.cells[target]
        if self._reaches(t, self.cell_name):
            raise ValueError("placing this cell here would create a cyclic reference")
        ref = Reference(t, tuple(origin), rotation, x_reflection, magnification, columns, rows, tuple(spacing))
        self.execute(AddReference(self.cell, ref))
        self._emit("cells")
        return ref

    def remove_reference(self, ref: Reference) -> None:
        self.execute(RemoveReference(self.cell, ref))
        self._emit("cells")

    # ---- view helpers ----------------------------------------------------------
    def extents(self) -> Optional[Tuple[float, float, float, float]]:
        return self.cell.bbox()

    # ---- files -----------------------------------------------------------------
    def save(self, path: Optional[str] = None) -> str:
        path = path or self.path
        if not path:
            raise ValueError("no file name")
        self.lib.save_mop(path)
        self.path = path
        self.stack.set_clean()
        return path

    @staticmethod
    def open_file(path: str, merge: bool = False) -> "Document":
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mop":
            return Document(Library.load_mop(path), path)
        if ext in (".gds", ".gdsii", ".gds2"):
            doc = Document(Library.read_gds(path, merge=merge), None)
            return doc
        if ext in (".oas", ".oasis"):
            return Document(Library.read_oas(path, merge=merge), None)
        raise ValueError(f"unsupported file type '{ext}' (use .mop, .gds or .oas)")

    def export_gds(self, path: str) -> None:
        self.lib.write_gds(path)

    def export_oas(self, path: str) -> None:
        self.lib.write_oas(path)

    def import_gds(self, path: str, merge: bool = False) -> List[str]:
        """Add all cells of a GDSII file to this library (names made unique). Returns the imported top-level cell names."""
        other = Library.read_gds(path, merge=merge)
        tops = [c.name for c in other.top_cells()]
        taken = set(self.lib.cells)
        rename: Dict[str, str] = {}
        for n in list(other.cells):
            new, i = n, 1
            while new in taken:
                new, i = f"{n}_{i}", i + 1
            taken.add(new)
            rename[n] = new
        with self.macro("Import GDSII"):
            for n, c in other.cells.items():
                c.name = rename[n]
                self.execute(AddCell(self.lib, c))
        for k, v in other.layers.items():
            self.lib.layers.setdefault(k, v)
        self.ensure_layers()
        self._emit("cells")
        return [rename[t] for t in tops]
