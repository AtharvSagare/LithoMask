"""Phase 8 - main-field / subfield partitioning and dose (shot-rank) assignment. Units: nm.

Conventions (from GenISys JEOL v30 note; machine specifics TODO-VERIFY):
- first main field origin = upper-left corner of the pattern extent; fields tile right then down.
- a shape that fits entirely inside a field extended by `overlap` on each side is written whole
  in that field (no stitching cut). Otherwise it is cut on the nominal field boundaries.
- subfields are assigned by trapezoid centre (index only, no cutting). Subfield size is tool-defined.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
import math
from shapely.geometry import box

from .fracture import Trap, fracture_geometry, snap_union, iter_polys
from ..geometry import Polygon


@dataclass
class FieldOptions:
    field_size: float = 1_000_000.0      # nm (JBX-9300FS 100 kV example: 1000 um) TODO-VERIFY per EOS mode
    subfield_size: Optional[float] = None  # nm; None -> no subfield indexing
    overlap: float = 0.0                 # nm, each side; must be <= field_size / 2
    extent: Optional[Tuple[float, float, float, float]] = None  # None -> pattern bbox


@dataclass
class FieldData:
    i: int                      # column index (0 = leftmost)
    j: int                      # row index (0 = top)
    x0: float
    y_top: float
    size: float
    traps: List[Trap] = field(default_factory=list)
    subfield: Dict[Tuple[int, int], int] = field(default_factory=dict)  # (si, sj) -> shot-trap count

    @property
    def area(self) -> float:
        return sum(t.area for t in self.traps)


@dataclass
class FieldPlan:
    fields: List[FieldData]
    extent: Tuple[float, float, float, float]
    cut_shapes: int             # shapes that had to be cut at field boundaries
    whole_shapes: int           # shapes placed uncut (incl. via overlap)

    @property
    def traps(self) -> List[Trap]:
        return [t for f in self.fields for t in f.traps]


def partition(polys: Sequence[Polygon], pu: float = 1.0, opts: Optional[FieldOptions] = None,
              merge: bool = True) -> FieldPlan:
    o = opts or FieldOptions()
    if not (0 <= o.overlap <= o.field_size / 2):
        raise ValueError("overlap must be in [0, field_size/2]")
    groups = snap_union(polys, pu)
    if not groups:
        return FieldPlan([], (0, 0, 0, 0), 0, 0)
    bs = [g.bounds for g in groups.values()]
    ext = o.extent or (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))
    xmin, ymin, xmax, ymax = ext
    F = o.field_size
    fields: Dict[Tuple[int, int], FieldData] = {}

    def get(i, j):
        if (i, j) not in fields:
            fields[(i, j)] = FieldData(i, j, xmin + i * F, ymax - j * F, F)
        return fields[(i, j)]

    def fi(x):  # column index of x
        return int(math.floor((x - xmin) / F + 1e-12))

    def fj(y):  # row index of y (rows go downward from ymax)
        return int(math.floor((ymax - y) / F - 1e-12))

    cut = whole = 0
    for (ly, dt), g in groups.items():
        for poly in iter_polys(g):
            bx0, by0, bx1, by1 = poly.bounds
            i, j = fi(0.5 * (bx0 + bx1)), fj(0.5 * (by0 + by1))
            fx0, fy_top = xmin + i * F, ymax - j * F
            if (bx0 >= fx0 - o.overlap and bx1 <= fx0 + F + o.overlap and
                    by1 <= fy_top + o.overlap and by0 >= fy_top - F - o.overlap):
                get(i, j).traps += fracture_geometry(poly, ly, dt, pu, merge)
                whole += 1
                continue
            cut += 1
            for ii in range(fi(bx0), fi(bx1 - 1e-9) + 1):
                for jj in range(fj(by1 - 1e-9), fj(by0) + 1):
                    x0, yt = xmin + ii * F, ymax - jj * F
                    piece = poly.intersection(box(x0, yt - F, x0 + F, yt))
                    if not piece.is_empty and piece.area > 0:
                        get(ii, jj).traps += fracture_geometry(piece, ly, dt, pu, merge)
    out = sorted(fields.values(), key=lambda f: (f.j, f.i))
    if o.subfield_size:
        S = o.subfield_size
        for f in out:
            for t in f.traps:
                cx, cy = t.centre
                k = (int((cx - f.x0) // S), int((f.y_top - cy) // S))
                f.subfield[k] = f.subfield.get(k, 0) + 1
    return FieldPlan(out, ext, cut, whole)


# ---------------------------------------------------------------- dose / shot rank
@dataclass
class DoseTable:
    """Maps (layer, datatype) or layer -> relative dose factor; each distinct factor becomes a class.
    mode: 'layer' | 'datatype' | 'dose' (documentation only; matches BEAMER's 'X to Class' wording)."""
    base_dose: float = 300.0                       # uC/cm^2
    factors: Dict[object, float] = field(default_factory=dict)  # key: layer int or (layer, datatype)
    max_classes: int = 64                          # TODO-VERIFY machine limit on shot ranks
    mode: str = "layer"

    def factor_of(self, layer: int, datatype: int) -> float:
        for k in ((layer, datatype), layer):
            if k in self.factors:
                return self.factors[k]
        return 1.0

    def classes(self, keys: Sequence[Tuple[int, int]]) -> Dict[float, int]:
        facs = sorted({round(self.factor_of(l, d), 6) for l, d in keys})
        if len(facs) > self.max_classes:
            raise ValueError(f"{len(facs)} dose classes exceed max_classes={self.max_classes}")
        return {f: n for n, f in enumerate(facs)}

    def class_of(self, layer: int, datatype: int, cmap: Dict[float, int]) -> int:
        return cmap[round(self.factor_of(layer, datatype), 6)]

    def to_jdi(self, keys: Sequence[Tuple[int, int]], name: str = "MOD001") -> str:
        """ASCII dose table in the MODULAT form seen in a public BEAMER quick-start
        (Cornell, 2010): `MOD001: MODULAT (( 0, d0 ) , ( 1, d1 ) ...`. Value meaning (absolute dose
        vs percent) and exact syntax are TODO-VERIFY against a real BEAMER-written .jdi."""
        cmap = self.classes(keys)
        items = " , ".join(f"( {n}, {self.base_dose * f:.2f} )" for f, n in cmap.items())
        return f"{name}: MODULAT ({items})\n"
