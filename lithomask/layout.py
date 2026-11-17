"""lithomask.layout - hierarchy (Cell/Reference/Library), GDSII I/O (via gdstk) and .mop JSON project files.

Conventions: internal unit nm (float). GDSII is written with user unit 1 um and database unit `dbu_nm` (default 1 nm);
vertices off the database grid are rounded to it on write (max error dbu/2).
Polygons with holes have no GDSII equivalent: gdstk's boolean 'not' splits them into hole-free pieces on write (verified: a ring
becomes 2 polygons). Total area is preserved; read_gds(..., merge=True) reassembles outer+hole. Zero-width keyhole slits found
in foreign files are repaired with shapely.make_valid.
Reference transform order (same as GDSII): x_reflection (mirror y -> -y), magnification, rotation (deg CCW), translation.
Array reference: element (i,j) is shifted by R(rotation)*(i*dx, +/-j*dy) (dy negated if x_reflection, no magnification) - the
gdstk/GDSII convention, verified against gdstk in tests (see Reference.offsets).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import shapely

from .geometry import Polygon, bbox_of

FORMAT_NAME = "lithomask-project"
FORMAT_VERSION = 1


@dataclass
class Reference:
    cell: "Cell"
    origin: Tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    x_reflection: bool = False
    magnification: float = 1.0
    columns: int = 1
    rows: int = 1
    spacing: Tuple[float, float] = (0.0, 0.0)  # (dx, dy) nm element pitch in the reference's own frame (see offsets())

    def offsets(self) -> List[Tuple[float, float]]:
        """Parent-frame displacement of every array element relative to `origin`.
        gdstk/GDSII convention (verified empirically against gdstk in tests): the element pitch (dx, dy) lives in the
        reference's own frame - rotated by `rotation`, dy negated when x_reflection is set, and NOT scaled by magnification."""
        t = math.radians(self.rotation)
        c, s = math.cos(t), math.sin(t)
        sy = -1.0 if self.x_reflection else 1.0
        out = []
        for j in range(self.rows):
            for i in range(self.columns):
                dx, dy = i * self.spacing[0], sy * j * self.spacing[1]
                out.append((c * dx - s * dy, s * dx + c * dy))
        return out


def _xform(p: Polygon, origin, rotation, x_reflection, magnification) -> Polygon:
    q = p
    if x_reflection:
        q = q.mirror("x", 0.0)
    if magnification != 1.0:
        q = q.scale(magnification)
    if rotation:
        q = q.rotate(rotation)
    return q.translate(origin[0], origin[1])


@dataclass
class Cell:
    name: str
    polygons: List[Polygon] = field(default_factory=list)
    references: List[Reference] = field(default_factory=list)

    def add(self, *items) -> "Cell":
        for it in items:
            if isinstance(it, Polygon):
                self.polygons.append(it)
            else:
                self.polygons.extend(it)
        return self

    def add_ref(self, cell: "Cell", origin=(0.0, 0.0), rotation=0.0, x_reflection=False, magnification=1.0,
                columns=1, rows=1, spacing=(0.0, 0.0)) -> Reference:
        r = Reference(cell, tuple(origin), rotation, x_reflection, magnification, columns, rows, tuple(spacing))
        self.references.append(r)
        return r

    def flatten(self, _stack: Optional[Tuple[str, ...]] = None) -> List[Polygon]:
        """All polygons of this cell with the hierarchy expanded."""
        stack = (_stack or ()) + (self.name,)
        out = [p.copy() for p in self.polygons]
        for r in self.references:
            if r.cell.name in stack:
                raise ValueError(f"cyclic reference: {' -> '.join(stack + (r.cell.name,))}")
            child = r.cell.flatten(stack)
            for dx, dy in r.offsets():
                for p in child:
                    out.append(_xform(p, (r.origin[0] + dx, r.origin[1] + dy), r.rotation, r.x_reflection, r.magnification))
        return out

    def bbox(self) -> Optional[Tuple[float, float, float, float]]:
        polys = self.flatten()
        return bbox_of(polys) if polys else None


@dataclass
class LayerInfo:
    name: str = ""
    color: str = "#4488ff"
    visible: bool = True
    locked: bool = False


@dataclass
class Library:
    name: str = "LITHOMASK"
    cells: Dict[str, Cell] = field(default_factory=dict)
    layers: Dict[Tuple[int, int], LayerInfo] = field(default_factory=dict)
    dbu_nm: float = 1.0
    plate: dict = field(default_factory=dict)      # PlateSettings.to_dict() (mask plate / wafer, see lithomask.plate); {} = unset

    def new_cell(self, name: str) -> Cell:
        if name in self.cells:
            raise ValueError(f"cell '{name}' exists")
        c = Cell(name)
        self.cells[name] = c
        return c

    def add_cell(self, cell: Cell) -> Cell:
        self.cells[cell.name] = cell
        return cell

    def top_cells(self) -> List[Cell]:
        used = {r.cell.name for c in self.cells.values() for r in c.references}
        return [c for n, c in self.cells.items() if n not in used]

    def flatten(self, cell: Optional[str] = None) -> List[Polygon]:
        if cell is None:
            tops = self.top_cells()
            if len(tops) != 1:
                raise ValueError(f"need exactly one top cell, found {[c.name for c in tops]}; pass cell=")
            return tops[0].flatten()
        return self.cells[cell].flatten()

    # ------------------------------------------------------------------ GDSII
    def write_gds(self, path: str) -> None:
        import gdstk

        lib = gdstk.Library(self.name, unit=1e-6, precision=self.dbu_nm * 1e-9)
        gcells = {n: lib.new_cell(n) for n in self.cells}
        to_um = 1e-3
        for n, c in self.cells.items():
            gc = gcells[n]
            for p in c.polygons:
                outer = gdstk.Polygon(p.outer * to_um, layer=p.layer, datatype=p.datatype)
                if p.holes:
                    holes = [gdstk.Polygon(h * to_um) for h in p.holes]
                    gc.add(*gdstk.boolean([outer], holes, "not", precision=self.dbu_nm * 1e-3,
                                          layer=p.layer, datatype=p.datatype))
                else:
                    gc.add(outer)
            for r in c.references:
                kw = dict(origin=(r.origin[0] * to_um, r.origin[1] * to_um), rotation=math.radians(r.rotation),
                          magnification=r.magnification, x_reflection=r.x_reflection)
                if r.columns > 1 or r.rows > 1:
                    kw.update(columns=r.columns, rows=r.rows, spacing=(r.spacing[0] * to_um, r.spacing[1] * to_um))
                gc.add(gdstk.Reference(gcells[r.cell.name], **kw))
        lib.write_gds(path)

    def write_oas(self, path: str) -> None:
        """Write OASIS using the same units, geometry and hierarchy as GDSII."""
        import gdstk

        lib = gdstk.Library(self.name, unit=1e-6, precision=self.dbu_nm * 1e-9)
        gcells = {n: lib.new_cell(n) for n in self.cells}
        to_um = 1e-3
        for n, c in self.cells.items():
            gc = gcells[n]
            for p in c.polygons:
                outer = gdstk.Polygon(p.outer * to_um, layer=p.layer, datatype=p.datatype)
                if p.holes:
                    holes = [gdstk.Polygon(h * to_um) for h in p.holes]
                    gc.add(*gdstk.boolean([outer], holes, "not", precision=self.dbu_nm * 1e-3,
                                          layer=p.layer, datatype=p.datatype))
                else:
                    gc.add(outer)
            for r in c.references:
                kw = dict(origin=(r.origin[0] * to_um, r.origin[1] * to_um), rotation=math.radians(r.rotation),
                          magnification=r.magnification, x_reflection=r.x_reflection)
                if r.columns > 1 or r.rows > 1:
                    kw.update(columns=r.columns, rows=r.rows, spacing=(r.spacing[0] * to_um, r.spacing[1] * to_um))
                gc.add(gdstk.Reference(gcells[r.cell.name], **kw))
        lib.write_oas(path)

    @staticmethod
    def read_gds(path: str, merge: bool = False) -> "Library":
        """Read a GDSII file. merge=True unions touching/overlapping polygons per (layer, datatype) in every cell, which
        also reassembles polygons-with-holes that were split into pieces on write (identity of individual polygons is lost)."""
        import gdstk

        g = gdstk.read_gds(path)
        k = g.unit * 1e9  # file length unit -> nm
        out = Library(name=g.name, dbu_nm=g.precision * 1e9)
        for gc in g.cells:
            out.cells[gc.name] = Cell(gc.name)
        for gc in g.cells:
            c = out.cells[gc.name]
            geoms = list(gc.polygons)
            for pth in gc.paths:
                geoms.extend(pth.to_polygons())
            for gp in geoms:
                c.polygons.extend(_repair(np.asarray(gp.points) * k, gp.layer, gp.datatype))
            if merge and c.polygons:
                groups: Dict[Tuple[int, int], List[Polygon]] = {}
                for p in c.polygons:
                    groups.setdefault(p.key, []).append(p)
                c.polygons = [q for (L, D), ps in groups.items()
                              for q in Polygon.from_shapely(shapely.union_all([p.to_shapely() for p in ps]), L, D)]
            for gr in gc.references:
                target = out.cells[gr.cell.name if hasattr(gr.cell, "name") else gr.cell]
                rep = gr.repetition
                common = dict(rotation=math.degrees(gr.rotation), x_reflection=bool(gr.x_reflection),
                              magnification=float(gr.magnification))
                o = (gr.origin[0] * k, gr.origin[1] * k)
                if rep.columns and rep.rows:
                    if rep.spacing is not None:
                        sp = (float(rep.spacing[0]) * k, float(rep.spacing[1]) * k)
                    else:  # rotated/skewed array stored as two parent-frame lattice vectors
                        t = math.radians(common["rotation"])
                        c_, s_ = math.cos(t), math.sin(t)
                        v1 = np.asarray(rep.v1, float) * k
                        v2 = np.asarray(rep.v2, float) * k
                        l1x = c_ * v1[0] + s_ * v1[1]            # R(-rot) v1 -> (dx, 0)
                        l2y = -s_ * v2[0] + c_ * v2[1]           # R(-rot) v2 -> (0, +/-dy)
                        sp = (float(l1x), float(-l2y if common["x_reflection"] else l2y))
                    c.references.append(Reference(target, o, columns=int(rep.columns), rows=int(rep.rows), spacing=sp, **common))
                else:
                    offs = rep.get_offsets() if rep.size > 0 else [(0.0, 0.0)]
                    for dx, dy in offs:
                        c.references.append(Reference(target, (o[0] + dx * k, o[1] + dy * k), **common))
        return out

    @staticmethod
    def read_oas(path: str, merge: bool = False) -> "Library":
        """Read OASIS.  OASIS uses the same geometry conversion as the GDSII reader."""
        import gdstk

        g = gdstk.read_oas(path)
        # Reuse the established GDS reader path through a temporary in-memory-compatible GDS file is not safe;
        # OASIS objects expose the same gdstk cell/polygon/reference API, so mirror the supported conversion.
        k = g.unit * 1e9
        out = Library(name=g.name, dbu_nm=g.precision * 1e9)
        for gc in g.cells:
            out.cells[gc.name] = Cell(gc.name)
        for gc in g.cells:
            c = out.cells[gc.name]
            for gp in gc.polygons:
                c.polygons.extend(_repair(np.asarray(gp.points) * k, gp.layer, gp.datatype))
            if merge and c.polygons:
                groups: Dict[Tuple[int, int], List[Polygon]] = {}
                for p in c.polygons:
                    groups.setdefault(p.key, []).append(p)
                c.polygons = [q for (L, D), ps in groups.items()
                              for q in Polygon.from_shapely(shapely.union_all([p.to_shapely() for p in ps]), L, D)]
            for gr in gc.references:
                target = out.cells[gr.cell.name if hasattr(gr.cell, "name") else gr.cell]
                rep = gr.repetition
                common = dict(rotation=math.degrees(gr.rotation), x_reflection=bool(gr.x_reflection),
                              magnification=float(gr.magnification))
                o = (gr.origin[0] * k, gr.origin[1] * k)
                if rep.columns and rep.rows and rep.spacing is not None:
                    sp = (float(rep.spacing[0]) * k, float(rep.spacing[1]) * k)
                    c.references.append(Reference(target, o, columns=int(rep.columns), rows=int(rep.rows), spacing=sp, **common))
                else:
                    offs = rep.get_offsets() if rep.size > 0 else [(0.0, 0.0)]
                    for dx, dy in offs:
                        c.references.append(Reference(target, (o[0] + dx * k, o[1] + dy * k), **common))
        return out

    # ------------------------------------------------------------------ .mop JSON
    def save_mop(self, path: str) -> None:
        d = {"format": FORMAT_NAME, "version": FORMAT_VERSION, "unit": "nm", "name": self.name, "dbu_nm": self.dbu_nm, "plate": self.plate,
             "layers": [{"layer": k[0], "datatype": k[1], **vars(v)} for k, v in self.layers.items()],
             "cells": []}
        for c in self.cells.values():
            d["cells"].append({
                "name": c.name,
                "polygons": [{"layer": p.layer, "datatype": p.datatype, "outer": p.outer.tolist(),
                              "holes": [h.tolist() for h in p.holes]} for p in c.polygons],
                "refs": [{"cell": r.cell.name, "origin": list(r.origin), "rotation": r.rotation,
                          "x_reflection": r.x_reflection, "magnification": r.magnification,
                          "columns": r.columns, "rows": r.rows, "spacing": list(r.spacing)} for r in c.references]})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)

    @staticmethod
    def load_mop(path: str) -> "Library":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("format") not in (FORMAT_NAME, "maskopc-project"):      # "maskopc-project" = files saved before the rename
            raise ValueError("not a lithomask project file")
        if d.get("version", 0) > FORMAT_VERSION:
            raise ValueError(f"project version {d['version']} newer than supported {FORMAT_VERSION}")
        lib = Library(d.get("name", "LITHOMASK"), dbu_nm=d.get("dbu_nm", 1.0), plate=dict(d.get("plate") or {}))
        for l in d.get("layers", []):
            lib.layers[(l["layer"], l["datatype"])] = LayerInfo(l.get("name", ""), l.get("color", "#4488ff"),
                                                                l.get("visible", True), l.get("locked", False))
        for cd in d["cells"]:
            lib.cells[cd["name"]] = Cell(cd["name"])
        for cd in d["cells"]:
            c = lib.cells[cd["name"]]
            for p in cd["polygons"]:
                c.polygons.append(Polygon(np.array(p["outer"]), [np.array(h) for h in p["holes"]], p["layer"], p["datatype"]))
            for r in cd["refs"]:
                c.references.append(Reference(lib.cells[r["cell"]], tuple(r["origin"]), r["rotation"], r["x_reflection"],
                                              r["magnification"], r["columns"], r["rows"], tuple(r["spacing"])))
        return lib


def _repair(points: np.ndarray, layer: int, datatype: int) -> List[Polygon]:
    """Turn a GDS boundary (possibly with a keyhole slit or minor defects) into valid Polygon(s)."""
    p = Polygon(points, [], layer, datatype)
    sp = p.to_shapely()
    if sp.is_valid and not _has_slit(points):
        return [p]
    g = shapely.make_valid(sp)
    return Polygon.from_shapely(g, layer, datatype) or [p]


def _has_slit(points: np.ndarray) -> bool:
    """True if some vertex coordinate occurs twice (keyhole bridge endpoints repeat)."""
    r = np.round(points, 6)
    return len({tuple(x) for x in r}) < len(r)
