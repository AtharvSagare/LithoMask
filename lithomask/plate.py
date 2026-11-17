"""Mask-plate and wafer settings (modelled on CleWin5's  View | Background settings... -> Mask / Wafer tabs).

No Qt here.  Units: this module stores sizes in millimetres (as CleWin's status bar does) and converts to the internal
nanometre unit with `MM`.  Geometry is nm everywhere else in LithoMask.

CleWin5 preset lists (read from CleWin5's cle5com.bpl resources, notes\\clewin_plate_wafer.md):
  mask  : Custom, 3x3, 4x4, 5x5, 6x6, 7x7, 9x9 inch   (square plates; status text "Mask size: %.0lfmm x %.0lfmm")
  wafer : 1, 2, 3 inch, 100 mm (4"), 125 mm (5"), 150 mm (6"), 200 mm (8"), 300 mm (12"), 450 mm (18")
CleWin only draws the outline and warns about objects outside the mask.  LithoMask does the same AND additionally checks the
layout / mask plate against the wafer, using the optical reduction factor (1 = 1:1 contact/proximity or 1x stepper).

Conventions: mask outline and wafer are centred on (cx, cy) in nm (default the layout origin 0,0).  Inch presets use the exact
conversion 1 inch = 25.4 mm (the nominal "inch" names of CleWin; real 4"+ wafers are 100/125/150/200/300/450 mm).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

MM = 1.0e6                    # nm per mm
INCH_MM = 25.4

MASK_PRESETS: Dict[str, Optional[Tuple[float, float]]] = {      # name -> (width_mm, height_mm); None = custom
    "Custom": None,
    **{f"{n} inch x {n} inch": (n * INCH_MM, n * INCH_MM) for n in (3, 4, 5, 6, 7, 9)},
}
WAFER_PRESETS: Dict[str, float] = {                              # name -> diameter in mm
    "1 inch": 1 * INCH_MM, "2 inch": 2 * INCH_MM, "3 inch": 3 * INCH_MM,
    '100 mm ("4 inch")': 100.0, '125 mm ("5 inch")': 125.0, '150 mm ("6 inch")': 150.0,
    '200 mm ("8 inch")': 200.0, '300 mm ("12 inch")': 300.0, '450 mm ("18 inch")': 450.0,
}


@dataclass
class PlateSettings:
    show_mask: bool = True                  # draw the mask outline in the background (CleWin: Show mask dimensions)
    mask_preset: str = "4 inch x 4 inch"
    mask_w_mm: float = 4 * INCH_MM          # used as-is when mask_preset == "Custom"
    mask_h_mm: float = 4 * INCH_MM
    show_wafer: bool = True                 # CleWin: Show wafer outline
    wafer_preset: str = '100 mm ("4 inch")'
    wafer_diam_mm: float = 100.0            # used as-is when wafer_preset == "Custom"
    edge_exclusion_mm: float = 0.0          # unusable rim of the wafer (resist edge bead etc.)
    reduction: float = 1.0                  # optical reduction M: wafer size = mask size / M   (1, 4, 5, ...)
    warn: bool = True                       # CleWin: warn about structures outside the mask
    cx_nm: float = 0.0                      # centre of mask outline / wafer in layout coordinates
    cy_nm: float = 0.0

    # ---- derived sizes ----
    def mask_size_mm(self) -> Tuple[float, float]:
        p = MASK_PRESETS.get(self.mask_preset)
        return p if p else (float(self.mask_w_mm), float(self.mask_h_mm))

    def wafer_diameter_mm(self) -> float:
        return float(WAFER_PRESETS.get(self.wafer_preset, self.wafer_diam_mm))

    def validate(self) -> None:
        w, h = self.mask_size_mm()
        if w <= 0 or h <= 0 or self.wafer_diameter_mm() <= 0:
            raise ValueError("mask and wafer sizes must be > 0")
        if self.reduction <= 0:
            raise ValueError("reduction factor must be > 0")
        if not 0 <= self.edge_exclusion_mm < self.wafer_diameter_mm() / 2:
            raise ValueError("edge exclusion must be >= 0 and smaller than the wafer radius")

    def mask_rect_nm(self) -> Tuple[float, float, float, float]:
        w, h = self.mask_size_mm()
        return (self.cx_nm - w * MM / 2, self.cy_nm - h * MM / 2, self.cx_nm + w * MM / 2, self.cy_nm + h * MM / 2)

    def wafer_radius_nm(self) -> float:
        return self.wafer_diameter_mm() * MM / 2

    def usable_radius_mm(self) -> float:
        return self.wafer_diameter_mm() / 2 - self.edge_exclusion_mm

    def mask_label(self) -> str:
        w, h = self.mask_size_mm()
        return f"Mask size: {w:.0f}mm x {h:.0f}mm"

    # ---- (de)serialisation for the .mop project ----
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "PlateSettings":
        base = PlateSettings()
        if not d:
            return base
        return PlateSettings(**{k: d[k] for k in asdict(base) if k in d})


@dataclass
class PlateIssue:
    code: str            # mask_overflow | wafer_overflow | mask_larger_than_wafer
    severity: str        # "error" (print will fail / be clipped) | "warning"
    text: str


@dataclass
class PlateReport:
    issues: List[PlateIssue] = field(default_factory=list)
    n_outside_mask: int = 0
    extent_mm: Optional[Tuple[float, float]] = None       # layout bbox size (w, h) in mm on the MASK
    wafer_extent_mm: Optional[Tuple[float, float]] = None  # same after dividing by the reduction (on the WAFER)

    @property
    def ok(self) -> bool:
        return not self.issues

    def text(self) -> str:
        return "\n".join(f"{'ERROR' if i.severity == 'error' else 'Warning'}: {i.text}" for i in self.issues)


def check_plate(polys: Sequence, plate: PlateSettings) -> PlateReport:
    """Check layout polygons (nm) against the mask outline and the wafer.  Only enabled parts (show_mask / show_wafer) are checked."""
    plate.validate()
    rep = PlateReport()
    polys = list(polys)
    w_mm, h_mm = plate.mask_size_mm()
    M = plate.reduction
    if plate.show_mask or plate.show_wafer:
        # --- plate versus wafer geometry (independent of the layout) ---
        if plate.show_mask and plate.show_wafer:
            half_diag_mm = math.hypot(w_mm, h_mm) / 2 / M     # mask corner as imaged onto the wafer
            if half_diag_mm > plate.usable_radius_mm() + 1e-9:
                fits_side = max(w_mm, h_mm) / M <= 2 * plate.usable_radius_mm()
                rep.issues.append(PlateIssue(
                    "mask_larger_than_wafer", "warning",
                    f"The full {w_mm:.1f} x {h_mm:.1f} mm mask plate imaged at {M:g}:1 ({w_mm / M:.1f} x {h_mm / M:.1f} mm) does not "
                    f"fit inside the usable {2 * plate.usable_radius_mm():.1f} mm wafer circle. "
                    + ("Only layouts that stay inside the circle will print completely." if fits_side else
                       "Parts of the plate will not print; keep the design inside the wafer circle.")))
    if not polys:
        return rep
    x0 = min(float(p.bbox[0]) for p in polys); y0 = min(float(p.bbox[1]) for p in polys)
    x1 = max(float(p.bbox[2]) for p in polys); y1 = max(float(p.bbox[3]) for p in polys)
    rep.extent_mm = ((x1 - x0) / MM, (y1 - y0) / MM)
    rep.wafer_extent_mm = (rep.extent_mm[0] / M, rep.extent_mm[1] / M)
    if plate.show_mask:
        mx0, my0, mx1, my1 = plate.mask_rect_nm()
        out = [p for p in polys if p.bbox[0] < mx0 or p.bbox[1] < my0 or p.bbox[2] > mx1 or p.bbox[3] > my1]
        rep.n_outside_mask = len(out)
        if out:
            rep.issues.append(PlateIssue("mask_overflow", "error",
                f"{len(out)} object(s) lie (partly) outside the {w_mm:.1f} x {h_mm:.1f} mm mask area; layout extent is "
                f"{rep.extent_mm[0]:.2f} x {rep.extent_mm[1]:.2f} mm. They would be cut off or missing on the mask."))
    if plate.show_wafer:
        R = plate.usable_radius_mm() * MM
        far = 0.0
        for p in polys:                                            # farthest vertex from the wafer centre, on the wafer scale
            for ring in [p.outer] + list(p.holes):
                for x, y in ring:
                    far = max(far, math.hypot(x - plate.cx_nm, y - plate.cy_nm))
        far /= M
        if far > R:
            rep.issues.append(PlateIssue("wafer_overflow", "error",
                f"The pattern reaches {far / MM:.2f} mm from the wafer centre at {M:g}:1, beyond the usable wafer radius "
                f"{R / MM:.2f} mm ({plate.wafer_diameter_mm():.1f} mm wafer, {plate.edge_exclusion_mm:g} mm edge exclusion). "
                f"The part outside the wafer will not print."))
    return rep


def layout_polygons(doc) -> List:
    """Flattened polygons of the active cell of an edit.Document."""
    return list(doc.cell.flatten())
