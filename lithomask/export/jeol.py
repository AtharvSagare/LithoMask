"""Phase 8 - JEOL52 (v3.0 / .v30) export.

Route B (always available): GDSII (snapped to Pattern Unit) + fractured preview GDS + .jdi dose table
+ BEAMER settings sheet + JDF template + README + JSON report. Conversion to .v30 is done in BEAMER.
Route A (native .v30 binary writer): NOT implemented - no public byte-level spec and no real sample
available yet. `write_v30` raises until a sample is parsed and byte-round-tripped (never guess offsets).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union
import json, math

from ..geometry import Polygon
from ..layout import Library
from .fracture import Trap
from .fields import FieldOptions, FieldPlan, DoseTable, partition
from .beamer_script import build_beamer_script, FRACTURE_MODES


@dataclass
class JeolOptions:
    pattern_unit: float = 1.0          # nm (JBX-9300FS 100 kV min = 1 nm; Purdue 8100FS default 0.5)
    shot_pitch: float = 4.0            # nm; set in SDF/JDF, NOT stored in v30. Should be integer x pattern_unit
    fields: FieldOptions = field(default_factory=FieldOptions)
    dose: DoseTable = field(default_factory=DoseTable)
    merge_rects: bool = True
    area_tol: float = 1e-3             # allowed relative area error after snap+fracture
    machine: str = "JBX-9300FS (100kV)"  # BEAMER MachineType string (manual example); TODO-VERIFY others
    eos_mode: str = "TODO"             # facility-specific; digit string goes to the script, else '1' + TODO-VERIFY
    # --- export_v30 parameters named in the BEAMER 4.6.6 manual (11.4.8.15); semantics: see the BEAMER dialog
    fracture_mode: str = "Conventional"  # 'Conventional' | 'LRFT' | 'Curved' (manual 4.2.7 / 11.4.8.15)
    memory_size: int = 256               # manual example value; TODO-VERIFY
    center_to_field: bool = False
    normalize_dose: bool = False
    preserve_dose_range: bool = True
    use_compaction: bool = True
    correct_lines: bool = False
    reserved_single_line_classes: int = 0
    threads: int = 4                     # lb.set_number_of_threads (manual 11.4.1)


@dataclass
class ExportReport:
    n_traps: int
    n_rect: int
    n_fields: int
    n_shots_est: int
    extent_nm: Tuple[float, float, float, float]
    total_area_nm2: float
    area_by_layer: Dict[str, float]
    max_rel_area_error: float
    cut_shapes: int
    whole_shapes: int
    warnings: List[str]
    ok: bool

    def summary(self) -> str:
        w, h = self.extent_nm[2] - self.extent_nm[0], self.extent_nm[3] - self.extent_nm[1]
        return (f"{self.n_traps} traps ({self.n_rect} rect) in {self.n_fields} field(s); ~{self.n_shots_est} shots; "
                f"extent {w/1e3:.3f} x {h/1e3:.3f} um; area {self.total_area_nm2/1e6:.4f} um^2; "
                f"max area err {self.max_rel_area_error:.2e}; {'OK' if self.ok else 'WARNINGS: ' + '; '.join(self.warnings)}")


def _polys_of(src: Union[Library, Sequence[Polygon]], cell: Optional[str] = None) -> List[Polygon]:
    return src.flatten(cell) if isinstance(src, Library) else list(src)


def estimate_shots(traps: Sequence[Trap], shot_pitch: float) -> int:
    """Upper-ish estimate: rows x columns of the widest edge per trapezoid. Real count depends on the tool."""
    n = 0
    for t in traps:
        rows = max(1, math.ceil((t.y1 - t.y0) / shot_pitch - 1e-9))
        cols = max(1, math.ceil(max(t.xr0 - t.xl0, t.xr1 - t.xl1) / shot_pitch - 1e-9))
        n += rows * cols
    return n


def prepare(src, opts: Optional[JeolOptions] = None, cell: Optional[str] = None) -> Tuple[FieldPlan, ExportReport]:
    """Snap, fracture, partition into fields. Returns the plan and a validation report."""
    o = opts or JeolOptions()
    polys = _polys_of(src, cell)
    warns: List[str] = []
    if o.fracture_mode not in FRACTURE_MODES:
        warns.append(f"shot_pitch/fracture: fracture_mode {o.fracture_mode!r} not in {FRACTURE_MODES}")
    ratio = o.shot_pitch / o.pattern_unit
    if abs(ratio - round(ratio)) > 1e-9:
        warns.append("shot_pitch is not an integer multiple of pattern_unit")
    plan = partition(polys, o.pattern_unit, o.fields, o.merge_rects)
    traps = plan.traps
    # area conservation per layer vs the snapped+unioned source
    from .fracture import snap_union
    src_area = {k: g.area for k, g in snap_union(polys, o.pattern_unit).items()}
    got: Dict[Tuple[int, int], float] = {}
    for t in traps:
        got[(t.layer, t.datatype)] = got.get((t.layer, t.datatype), 0.0) + t.area
    worst = 0.0
    for k, a in src_area.items():
        if a > 0:
            worst = max(worst, abs(got.get(k, 0.0) - a) / a)
    if worst > o.area_tol:
        warns.append(f"area not conserved: max rel error {worst:.2e} > {o.area_tol:.1e}")
    if len(plan.fields) > 1 and plan.cut_shapes:
        warns.append(f"{plan.cut_shapes} shape(s) cut at main-field boundaries (stitching risk); consider overlap")
    rep = ExportReport(len(traps), sum(t.is_rect for t in traps), len(plan.fields), estimate_shots(traps, o.shot_pitch),
                       plan.extent, sum(got.values()), {f"{k[0]}/{k[1]}": v for k, v in got.items()},
                       worst, plan.cut_shapes, plan.whole_shapes, warns,
                       ok=not any(w.startswith("area") or w.startswith("shot_pitch") for w in warns))
    return plan, rep


def write_v30(*a, **k):
    """Route A placeholder. TODO-VERIFY: needs a real .v30 sample (parse -> rewrite -> byte compare)."""
    raise NotImplementedError("Native .v30 writer is blocked: no verified binary layout. Use export_route_b() + BEAMER.")


def read_v30(*a, **k):
    raise NotImplementedError("Native .v30 reader is blocked: needs a real sample file.")


def export_route_b(src, outdir: str, name: str = "mask", opts: Optional[JeolOptions] = None,
                   cell: Optional[str] = None) -> ExportReport:
    """Write GDSII + preview + .jdi + settings + JDF template + README + report into outdir."""
    o = opts or JeolOptions()
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    polys = _polys_of(src, cell)
    plan, rep = prepare(polys, o)

    # 1) GDSII for BEAMER: original polygons snapped to the pattern unit (user unit 1 um, db 1 nm via Library)
    lib = Library(name=name.upper())
    top = lib.new_cell(name)
    top.add([p.snap(o.pattern_unit) for p in polys])
    lib.write_gds(str(out / f"{name}.gds"))

    # 2) fractured preview (what our fracturer would shoot); one cell per main field
    prev = Library(name=f"{name.upper()}_FRAC")
    pc = prev.new_cell(f"{name}_frac")
    pc.add([t.to_polygon() for t in plan.traps])
    prev.write_gds(str(out / f"{name}_fractured_preview.gds"))

    # 3) dose table, settings, JDF template
    keys = sorted({(t.layer, t.datatype) for t in plan.traps})
    cmap = o.dose.classes(keys) if keys else {}
    (out / f"{name}.jdi").write_text(o.dose.to_jdi(keys) if keys else "", encoding="ascii")
    F, S = o.fields.field_size, o.fields.subfield_size
    settings = [
        f"BEAMER Export settings for {name}  (Layout BEAMER, format: JEOL v30 / 'JEOL 52 v3.0')",
        f"Machine type            : {o.machine}   (EOS mode: {o.eos_mode})",
        f"Pattern Unit [nm]       : {o.pattern_unit}   (must equal the unit entered in the JEOL tool/JDF)",
        f"Shot Pitch [nm]         : {o.shot_pitch}   (integer multiple of Pattern Unit; set in SDF/JDF, not in v30)",
        f"Main field size [um]    : {F/1e3}   (TODO-VERIFY for your EOS mode)",
        f"Main field overlap [um] : {o.fields.overlap/1e3}",
        f"Subfield size [um]      : {'tool default' if not S else S/1e3}",
        "Extent                  : Automatic (or User, larger than layout only)",
        f"Fracture mode           : {o.fracture_mode}   (manual options: Conventional / LRFT / Curved)",
        f"Memory size / EOS mode  : {o.memory_size} / {o.eos_mode}   (TODO-VERIFY; facility specific)",
        f"Compaction / Normalize / Preserve dose range / Correct lines / Center to field: "
        f"{o.use_compaction} / {o.normalize_dose} / {o.preserve_dose_range} / {o.correct_lines} / {o.center_to_field}",
        f"Reserved single-line classes: {o.reserved_single_line_classes}",
        "Dose mapping            : Layer to Class (or Datatype to Class)",
        "Dose classes (layer/datatype -> class): " + (", ".join(f"{l}/{d}->{o.dose.class_of(l, d, cmap)}" for l, d in keys) or "none"),
        f"Base dose [uC/cm^2]     : {o.dose.base_dose}",
        "", "Our own estimate (compare with BEAMER's log):", rep.summary(),
    ]
    (out / f"{name}_beamer_settings.txt").write_text("\n".join(settings) + "\n", encoding="utf-8")
    jdf = [
        "; JDF template - TODO-VERIFY every keyword against your lab's JDF/SDF manual.",
        "; Keywords below are taken from a public Stanford SNSF slide (SHOT, RESIST, EOS, STDCUR, P(n)).",
        f"; Pattern Unit {o.pattern_unit} nm / Shot Pitch {o.shot_pitch} nm -> SHOT value = shot pitch in machine resolution units.",
        "LAYER    1", f"    P(1) '{name}.v30' (0,0)",
        "    SHOT A, <shot_pitch_in_resolution_units>", "    RESIST <dose>,<dose>,A",
        "    EOS <mode>, '<eos_file>'", "    STDCUR <nA>", "", f"; Paste the contents of {name}.jdi (dose table) into the JDF.",
    ]
    (out / f"{name}.jdf.template").write_text("\n".join(jdf) + "\n", encoding="ascii")
    (out / f"{name}_beamer_job.py").write_text(build_beamer_script(name, o, keys), encoding="utf-8")
    readme = f"""# {name} - JEOL52 v3.0 export package (Route B)

Files
- {name}.gds                      GDSII for BEAMER (snapped to {o.pattern_unit} nm). Layer/datatype preserved.
- {name}_fractured_preview.gds    Our trapezoid fracture, for visual comparison only. Not machine data.
- {name}.jdi                      Dose table (MODULAT) - syntax TODO-VERIFY.
- {name}_beamer_settings.txt      Values to type into the BEAMER Export (v30) dialog.
- {name}.jdf.template             Job-deck skeleton, TODO-VERIFY.
- {name}_beamer_job.py            Optional BEAMERpy script (import GDS -> heal -> FDA -> export_v30) built from the
                                  BEAMER 4.6.6 manual's Python reference; Python 2.6 syntax. Use instead of the GUI steps.
- {name}_report.json              Validation numbers (shots, extent, area, warnings).

JEOL hand-off and required extensions
1. Open {name}.gds in Layout BEAMER -> Export -> JEOL v30 -> enter the settings sheet values -> save {name}.v30.
   (Scripted alternative: set BEAMER_BIN and run {name}_beamer_job.py with BEAMER's Python; check its TODO-VERIFY lines.)
   BEAMER writes the machine pattern {name}.v30 and matching dose table {name}.jdi in the same directory.
2. Copy {name}.jdf.template to a facility-approved <job_name>.jdf and verify every keyword against that facility's
   JDF template.  The JDF references {name}.v30 and includes the contents of {name}.jdi.
3. Create or select the facility-approved <schedule_name>.sdf.  The SDF supplies the shot pitch and exposure
   settings; shot pitch is not stored in a .v30 file.  Neither the JDF nor SDF is generated by BEAMER.

Do not submit a .gds, .jdf.template, or _fractured_preview.gds directly to the JEOL writer.  The writer input is the
BEAMER-generated .v30, with its matching .jdi and the facility-validated .jdf/.sdf files.

Native .v30 writing (Route A) is NOT provided: no public binary spec and no real sample were available.
To enable it, supply one real .v30 plus its .jdi/.jdf; the writer will be built by parse -> rewrite -> byte-compare.

Validation: {rep.summary()}
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    (out / f"{name}_report.json").write_text(json.dumps(asdict(rep), indent=2), encoding="utf-8")
    return rep
