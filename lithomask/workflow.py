"""Phase 9 - headless workflow shared by the GUI and scripts: process settings -> OPC -> print simulation -> document.

No Qt here.  The GUI (lithomask.gui.opcui / exportui) only collects settings and shows results; everything it runs is a
function in this module, so the same path is covered by plain pytest and by examples\\end_to_end_demo.py.

Units: nm everywhere (database unit 1 nm).  Conventions follow lithomask.opc.common: `target` = the shapes that should PRINT;
tone 'clear_field' = target polygons are chrome on a clear mask, 'dark_field' = target polygons are openings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import edit
from .geometry import Polygon, bbox_of
from .opc import (InverseOptions, ModelOptions, OPCOptions, OPCResult, ProcessModel, RuleOptions, correct)
from .optics import Imager, Source
from .resist import PrintResult

LayerKey = Tuple[int, int]
SOURCES = ("conventional", "annular", "dipole", "quasar")
STAGES = ("model", "rule", "inverse")
MAX_SIM_PIXELS = 6_000_000          # refuse simulations whose grid would exceed this (memory / time guard)


class OPCCancelled(Exception):
    """Raised by run_opc when the cancel callback fired."""


# ======================================================================================
# Settings
# ======================================================================================
@dataclass
class ProcessSettings:
    wavelength: float = 193.0        # nm
    na: float = 0.75
    source: str = "conventional"     # conventional | annular | dipole | quasar
    sigma: float = 0.6               # conventional sigma, or OUTER sigma for the other shapes
    sigma_in: float = 0.4            # INNER sigma (annular / dipole / quasar)
    tone: str = "clear_field"        # clear_field | dark_field
    anchor_cd: Optional[float] = None   # nm; None = estimate from the target (see auto_anchor_cd)
    anchor_shape: str = "line"       # 'line' (isolated line) | 'hole' (isolated square) for the threshold anchor
    pixel: float = 8.0               # simulation pixel (nm)
    margin: float = 480.0            # simulation margin around the layout (nm)

    def make_source(self) -> Source:
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        if self.source == "conventional":
            return Source.conventional(self.sigma)
        if not 0 <= self.sigma_in < self.sigma:
            raise ValueError("inner sigma must be smaller than outer sigma")
        return getattr(Source, self.source)(self.sigma_in, self.sigma)

    def make_imager(self) -> Imager:
        if self.wavelength <= 0 or not 0 < self.na <= 1.5:
            raise ValueError("wavelength must be > 0 and 0 < NA <= 1.5")
        return Imager(wavelength=self.wavelength, na=self.na, source=self.make_source())

    def resolved_cd(self, polys: Optional[Sequence[Polygon]] = None) -> float:
        if self.anchor_cd:
            return float(self.anchor_cd)
        if not polys:
            raise ValueError("anchor_cd is not set and there is no target to estimate it from")
        return auto_anchor_cd(polys)

    def build(self, polys: Optional[Sequence[Polygon]] = None) -> ProcessModel:
        """Constant-threshold ProcessModel anchored so an isolated feature of `anchor_cd` prints at its drawn size."""
        if self.tone not in ("clear_field", "dark_field") or self.anchor_shape not in ("line", "hole"):
            raise ValueError("tone must be clear_field|dark_field and anchor_shape line|hole")
        return ProcessModel.anchored(self.make_imager(), self.resolved_cd(polys), self.tone, self.anchor_shape,
                                     self.pixel, self.margin)


def auto_anchor_cd(polys: Sequence[Polygon]) -> float:
    """Estimate of the critical dimension: the smallest feature width, taken as 2 x the inscribed radius of each polygon
    (found by shrinking until it vanishes) and minimised over polygons.  A heuristic - the dialog shows it so the user can
    override it; for pad+line shapes (dog-bones) enter the line width yourself."""
    best = float("inf")
    for p in polys:
        s = p.to_shapely()
        if s.is_empty or s.area <= 0:
            continue
        lo, hi = 0.0, 0.5 * min(s.bounds[2] - s.bounds[0], s.bounds[3] - s.bounds[1])
        for _ in range(24):
            mid = 0.5 * (lo + hi)
            if s.buffer(-mid, join_style=2).is_empty:
                hi = mid
            else:
                lo = mid
        if lo > 0:
            best = min(best, 2.0 * lo)
    if best == float("inf"):
        raise ValueError("target has no polygons with area")
    return round(best, 1)


# Named starting points; the GUI shows them in a combo, scripts can use them directly.
PRESETS: Dict[str, ProcessSettings] = {
    "193 nm, NA 0.75, sigma 0.6 - clear-field lines": ProcessSettings(),
    "193 nm, NA 0.75, sigma 0.6 - dark-field contacts": ProcessSettings(tone="dark_field", anchor_shape="hole"),
    "193 nm, NA 0.85, annular 0.5/0.8 - clear-field lines": ProcessSettings(na=0.85, source="annular", sigma=0.8,
                                                                            sigma_in=0.5),
    "365 nm i-line, NA 0.55, sigma 0.6 - clear-field": ProcessSettings(wavelength=365.0, na=0.55, margin=900.0,
                                                                       pixel=16.0),
}


@dataclass
class RunSettings:
    process: ProcessSettings = field(default_factory=ProcessSettings)
    stage: str = "model"             # model | rule | inverse   (one main stage)
    sraf: bool = False               # add assist features first
    max_iter: int = 10
    gain: float = 0.6
    tol: float = 1.0                 # nm
    inverse_iterations: int = 40
    target_layer: LayerKey = (1, 0)
    mask_layer: LayerKey = (100, 0)  # corrected main features are written here
    sraf_layer: LayerKey = (101, 0)  # assist features (and nothing else) are written here

    def validate(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        if len({tuple(self.target_layer), tuple(self.mask_layer), tuple(self.sraf_layer)}) != 3:
            raise ValueError("target, mask and SRAF layers must all be different")
        if self.max_iter < 1 or self.inverse_iterations < 1:
            raise ValueError("iteration counts must be >= 1")

    def to_options(self, cd: float, progress=None) -> OPCOptions:
        self.validate()
        stages = (("sraf",) if self.sraf else ()) + (self.stage,)
        rule = RuleOptions(serif=0.0, hammer_ext=round(0.12 * cd, 1)) if self.stage == "rule" else RuleOptions()
        return OPCOptions(stages=stages, rule=rule,
                          model=ModelOptions(max_iter=self.max_iter, gain=self.gain, tol=self.tol),
                          inverse=InverseOptions(pixel=self.process.pixel, iterations=self.inverse_iterations),
                          progress=progress)


@dataclass
class RunResult:
    settings: RunSettings
    process_model: ProcessModel
    anchor_cd: float
    target: List[Polygon]
    opc: OPCResult
    print_before: PrintResult        # simulated print of the uncorrected target used as the mask
    print_after: PrintResult         # simulated print of the corrected mask (incl. assists)

    @property
    def mask(self) -> List[Polygon]:
        return self.opc.mask

    def summary(self) -> str:
        b, a = self.opc.epe_before, self.opc.epe_after
        m = "MRC ok" if self.opc.mrc.get("ok", True) else "MRC violations"
        return (f"stage {self.opc.stage_used}: EPE RMS {b['rms']:.1f} -> {a['rms']:.1f} nm, "
                f"max {b['max']:.1f} -> {a['max']:.1f} nm; {len(self.opc.polygons)} main + {len(self.opc.srafs)} assist "
                f"polygons; {m}")


# ======================================================================================
# Running
# ======================================================================================
def precheck_size(polys: Sequence[Polygon], proc: ProcessSettings, max_pixels: int = MAX_SIM_PIXELS) -> None:
    """Cheap grid-size estimate from the layout bbox (no model needed): (extent + 2 margin) / pixel per axis."""
    x0, y0, x1, y1 = bbox_of(list(polys))
    nx = (x1 - x0 + 2 * proc.margin) / proc.pixel
    ny = (y1 - y0 + 2 * proc.margin) / proc.pixel
    if nx * ny > max_pixels:
        raise ValueError(f"simulation grid would be about {nx:.0f} x {ny:.0f} pixels ({nx * ny / 1e6:.1f} M, limit "
                         f"{max_pixels / 1e6:.0f} M). Use a larger pixel size or a smaller region of interest.")


def check_size(polys: Sequence[Polygon], pm: ProcessModel, max_pixels: int = MAX_SIM_PIXELS) -> None:
    g = pm.grid_for(list(polys))
    if g.nx * g.ny > max_pixels:
        raise ValueError(f"simulation grid would be {g.nx} x {g.ny} pixels ({g.nx * g.ny / 1e6:.1f} M, limit "
                         f"{max_pixels / 1e6:.0f} M). Use a larger pixel size or a smaller region of interest.")


def run_opc(polys: Sequence[Polygon], settings: Optional[RunSettings] = None,
            progress: Optional[Callable[[str, int, float, float], None]] = None,
            cancel: Optional[Callable[[], bool]] = None) -> RunResult:
    """Correct `polys` (target shapes) and simulate the print before/after.  `progress(stage, iter, rms, max)` is called per
    iteration; `cancel()` returning True aborts with OPCCancelled."""
    s = settings or RunSettings()
    s.validate()
    target = list(polys)
    if not target:
        raise ValueError("no target polygons on the selected layer")
    cancelled = lambda: bool(cancel and cancel())
    cd = s.process.resolved_cd(target)
    precheck_size(target, s.process)             # BEFORE building the model: tiny pixels make the SOCS kernels huge
    pm = s.process.build(target)
    check_size(target, pm)

    def cb(stage, it, rms, mx):
        if progress:
            progress(stage, it, rms, mx)
        return False if cancelled() else True

    if cancelled():
        raise OPCCancelled()
    res = correct(target, pm, s.to_options(cd, cb))
    if cancelled():
        raise OPCCancelled()
    grid = pm.grid_for(target + res.mask)
    before = pm.simulate(target, grid)
    if cancelled():
        raise OPCCancelled()
    after = pm.simulate(res.mask, grid)
    return RunResult(s, pm, cd, target, res, before, after)


# ======================================================================================
# Document helpers
# ======================================================================================
def polygons_on_layer(doc, key: LayerKey) -> List[Polygon]:
    """Flattened polygons of the active cell (instances included) on layer/datatype `key`."""
    return [p for p in doc.cell.flatten() if p.key == tuple(key)]


def layer_counts(doc) -> Dict[LayerKey, int]:
    out: Dict[LayerKey, int] = {}
    for p in doc.cell.flatten():
        out[p.key] = out.get(p.key, 0) + 1
    return dict(sorted(out.items()))


def apply_to_document(doc, run: RunResult) -> Tuple[int, int]:
    """Write the corrected mask / assists to their layers of the active cell as ONE undoable step; polygons already on
    those layers (a previous run) are replaced.  Returns (n_main, n_sraf)."""
    s = run.settings
    main = edit.with_layer(run.opc.polygons, *s.mask_layer)
    sraf = edit.with_layer(run.opc.srafs, *s.sraf_layer)
    old = [p for p in doc.cell.polygons if p.key in (tuple(s.mask_layer), tuple(s.sraf_layer))]
    with doc.macro("Run OPC"):
        doc.replace(old, main + sraf, "Run OPC")
        doc.set_layer_info(tuple(s.mask_layer), name="OPC mask", color="#ffb000")
        if sraf:
            doc.set_layer_info(tuple(s.sraf_layer), name="OPC SRAF", color="#40d0a0")
    return len(main), len(sraf)


def write_verification(run: RunResult, outdir: str, window: bool = False, name: str = "OPC") -> Dict[str, str]:
    """Phase 7 verification of the corrected mask (HTML + PNG); returns the paths dict of verify.write_report."""
    from .verify import VerifyOptions, WindowOptions, verify, write_report
    opts = VerifyOptions()
    if window:
        opts.window = WindowOptions(doses=(0.9, 1.0, 1.1), defoci=(-200.0, 0.0, 200.0), n_gauges=2)
    os.makedirs(outdir, exist_ok=True)
    return write_report(verify(run.target, run.mask, run.process_model, opts, name=name), outdir)
