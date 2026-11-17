"""Interchange exports corresponding to the CleWin Save As formats.

All geometry coordinates are nm internally.  CIF is written as standard CIF 2.0
at 1 nm resolution (DS scale 1/10); DXF contains closed outline polylines in
micrometres.  Both formats are intentionally flattened: this avoids format-specific
reference-transform restrictions and makes the delivered geometry explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import gdstk

from ..geometry import Polygon
from ..layout import Library


@dataclass(frozen=True)
class CifOptions:
    """CleWin-compatible Save As choices.

    LithoMask does not generate CleWin private CIF comments, so
    ``use_clewin_extensions`` is retained only to make the export choice explicit.
    CIF and DXF exports are always flattened, equivalent to CleWin's flatten option.
    """
    use_clewin_extensions: bool = False
    flatten: bool = True
    remove_empty_symbols: bool = True
    remove_unused_symbols: bool = True


def _top_polygons(lib: Library, cell: Optional[str] = None) -> Sequence[Polygon]:
    return lib.flatten(cell)


def _cif_pieces(p: Polygon) -> Sequence[gdstk.Polygon]:
    """Return hole-free GDS-style pieces so a CIF export preserves hole area."""
    outer = gdstk.Polygon(p.outer * 1e-3)
    holes = [gdstk.Polygon(h * 1e-3) for h in p.holes]
    return gdstk.boolean([outer], holes, "not", precision=1e-6) if holes else [outer]


def write_cif(lib: Library, path: str, cell: Optional[str] = None, options: Optional[CifOptions] = None) -> None:
    """Write flattened, standard CIF 2.0 polygons at 1 nm resolution.

    Layer names use ``Library.layers`` when present, otherwise ``L<layer>``.  CIF
    has no datatype field; a nonzero datatype is kept in a standard comment.
    """
    options = options or CifOptions()
    _ = options  # Options mirror the UI and document the intentionally flattened writer.
    groups: Dict[Tuple[int, int], list] = {}
    for p in _top_polygons(lib, cell):
        groups.setdefault((p.layer, p.datatype), []).append(p)
    name = cell or lib.top_cells()[0].name
    lines = ["(CIF written by LithoMask);", "(units: 1 nm; standard CIF DS scale 1/10);", "DS1 1 10;", f"9 {name};"]
    for (layer, datatype), polys in sorted(groups.items()):
        info = lib.layers.get((layer, datatype))
        layer_name = (info.name if info and info.name else f"L{layer}").replace(";", "_").replace(" ", "_")
        lines.append(f"L {layer_name};")
        if datatype:
            lines.append(f"(LithoMask datatype {datatype};)")
        for p in polys:
            for piece in _cif_pieces(p):
                pts = piece.points * 1e3  # um -> nm; DS scale converts nm to CIF base unit
                nums = " ".join(f"{int(round(x))} {int(round(y))}" for x, y in pts)
                lines.append(f"P {nums};")
    lines.extend(["DF;", "C 1;", "E"])
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")


def write_dxf(lib: Library, path: str, cell: Optional[str] = None) -> None:
    """Write flattened closed LWPOLYLINE outlines in micrometres.

    DXF does not carry mask polygon fill/hole semantics portably.  Outer and hole
    contours are both written as closed outlines on their numeric layer names.
    """
    lines = ["0", "SECTION", "2", "ENTITIES"]
    for p in _top_polygons(lib, cell):
        for ring in (p.outer, *p.holes):
            lines += ["0", "LWPOLYLINE", "8", str(p.layer), "90", str(len(ring)), "70", "1"]
            for x, y in ring:
                lines += ["10", f"{x * 1e-3:.9g}", "20", f"{y * 1e-3:.9g}"]
    lines += ["0", "ENDSEC", "0", "EOF"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")


def write_layer_map(lib: Library, path: str) -> None:
    """Write CleWin-style readable layer-name sidecar for GDSII/OASIS handoff."""
    keys = sorted(lib.layers) or sorted({p.key for c in lib.cells.values() for p in c.polygons})
    lines = ["# LithoMask Layer Map"]
    for layer, datatype in keys:
        info = lib.layers.get((layer, datatype))
        name = info.name if info and info.name else f"Layer {layer}"
        suffix = f" datatype {datatype}" if datatype else ""
        lines.append(f"{layer} {name}{suffix}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
