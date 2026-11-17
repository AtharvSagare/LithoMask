"""OPC shared pieces: ProcessModel, options, EPE measurement, MRC check, result container.

Conventions
  * `target` polygons = the intended PRINTED shapes (resist features). For tone='clear_field' they are chrome (absorber) on
    a clear mask and the printed feature is the dark region of the aerial image; for tone='dark_field' they are openings and
    the printed feature is the bright region.
  * EPE (nm) = signed distance along the OUTWARD normal of the target edge from the target edge to the simulated resist
    contour: positive = printed feature too large (extends beyond target), negative = too small / pulled back.
  * Fragment / mask shift delta (nm) is along the outward normal: positive = mask edge moved outward (feature grows).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage
from shapely.ops import unary_union

from ..geometry import Polygon, bbox_of, union
from ..optics import Grid, Imager
from ..resist import ConstantThreshold, PrintResult, ResistModel, simulate_print


# ======================================================================================
# Process model
# ======================================================================================
@dataclass
class ProcessModel:
    imager: Imager
    resist: ResistModel
    tone: str = "clear_field"
    pixel: float = 8.0
    margin: float = 480.0              # simulation margin around the layout (nm); keep >= ~ lambda/NA * 1.5
    supersample: int = 4

    def grid_for(self, polys: Sequence[Polygon]) -> Grid:
        return Grid.from_bbox(bbox_of(list(polys)), self.pixel, self.margin)

    def simulate(self, mask: Sequence[Polygon], grid: Grid) -> PrintResult:
        return simulate_print(mask, self.imager, self.resist, grid, self.tone, supersample=self.supersample)

    def inside_feature(self, F: np.ndarray, level: float) -> np.ndarray:
        """True where the printed resist feature is (dark region for clear-field, bright for dark-field)."""
        return F < level if self.tone == "clear_field" else F >= level

    @staticmethod
    def anchored(imager: Imager, cd: float, tone: str = "clear_field", shape: str = "line", pixel: float = 8.0,
                 margin: float = 480.0) -> "ProcessModel":
        """Constant-threshold model whose threshold makes an ISOLATED `cd`-wide line (shape='line') or `cd` x `cd` hole /
        island (shape='hole') print at its drawn size (anchor feature)."""
        from .. import geometry as g
        half = 1500.0 if shape == "line" else cd / 2
        poly = g.rectangle(-cd / 2, -half, cd / 2, half)
        grid = Grid(-1000.0, -1000.0, int(2000 / pixel), int(2000 / pixel), pixel)
        I = imager.image([poly], grid, tone=tone)
        thr = float(value_at(I, grid, cd / 2, 0.0))
        return ProcessModel(imager, ConstantThreshold(thr), tone, pixel, margin)


def value_at(F: np.ndarray, grid: Grid, x, y, order: int = 3):
    ix = (np.asarray(x, float) - grid.x0) / grid.pixel - 0.5
    iy = (np.asarray(y, float) - grid.y0) / grid.pixel - 0.5
    return ndimage.map_coordinates(F, [np.atleast_1d(iy), np.atleast_1d(ix)], order=order, mode="nearest")[0] \
        if np.ndim(x) == 0 else ndimage.map_coordinates(F, [iy, ix], order=order, mode="nearest")


# ======================================================================================
# EPE
# ======================================================================================
def measure_epe(F: np.ndarray, level: float, grid: Grid, points: np.ndarray, normals: np.ndarray,
                inside_fn: Callable[[np.ndarray, float], np.ndarray], search: float = 150.0) -> np.ndarray:
    """EPE at `points` (N,2) along unit `normals` (N,2) (outward). Nearest level crossing within +-search; if none the value
    saturates at +search (feature present everywhere there) or -search (absent)."""
    step = grid.pixel / 4.0
    offs = np.arange(-search, search + 0.5 * step, step)
    pts = points[:, None, :] + normals[:, None, :] * offs[None, :, None]
    ix = (pts[..., 0] - grid.x0) / grid.pixel - 0.5
    iy = (pts[..., 1] - grid.y0) / grid.pixel - 0.5
    v = ndimage.map_coordinates(F, [iy.ravel(), ix.ravel()], order=3, mode="nearest").reshape(len(points), len(offs))
    d = v - level
    out = np.empty(len(points))
    c0 = len(offs) // 2
    for k in range(len(points)):
        dk = d[k]
        idx = np.flatnonzero(dk[:-1] * dk[1:] < 0)
        if len(idx) == 0:
            inside = bool(inside_fn(np.array([v[k, c0]]), level)[0])
            out[k] = search if inside else -search
            continue
        pos = offs[idx] + dk[idx] / (dk[idx] - dk[idx + 1]) * step
        out[k] = pos[np.argmin(np.abs(pos))]
    return out


def epe_stats(epe: np.ndarray) -> Dict[str, float]:
    e = np.asarray(epe, float)
    return {"rms": float(np.sqrt(np.mean(e ** 2))) if len(e) else 0.0,
            "max": float(np.max(np.abs(e))) if len(e) else 0.0, "mean": float(np.mean(e)) if len(e) else 0.0, "n": int(len(e))}


# ======================================================================================
# Mask rule check (geometry only, shapely based)
# ======================================================================================
def mrc_check(polys: Sequence[Polygon], min_width: float = 0.0, min_space: float = 0.0, tol_area: float = 1.0) -> Dict[str, float]:
    """Counts MRC violations on a polygon set (nm). Width: area lost by a mitre opening of radius min_width/2; space: area gained
    by a mitre closing of radius min_space/2. Returns {'width_violations_area', 'space_violations_area', 'ok'}."""
    if not polys:
        return {"width_violations_area": 0.0, "space_violations_area": 0.0, "ok": True}
    U = unary_union([p.to_shapely() for p in polys])
    wa = sa = 0.0
    if min_width > 0:
        op = U.buffer(-min_width / 2, join_style=2).buffer(min_width / 2, join_style=2)
        wa = max(0.0, U.area - op.area)
    if min_space > 0:
        cl = U.buffer(min_space / 2, join_style=2).buffer(-min_space / 2, join_style=2)
        sa = max(0.0, cl.area - U.area)
    return {"width_violations_area": wa, "space_violations_area": sa, "ok": wa <= tol_area and sa <= tol_area}


# ======================================================================================
# Options and result
# ======================================================================================
@dataclass
class FragmentOptions:
    max_len: float = 120.0          # longest run fragment
    min_len: float = 40.0           # shortest fragment (= minimum jog length)
    corner_len: float = 40.0        # length of the fragment next to every corner
    lineend_max: float = 220.0      # edges up to this length between two convex corners are line ends


@dataclass
class MRCOptions:
    grid: float = 1.0               # mask address grid (nm): shifts are snapped to it
    max_bias: float = 80.0          # |total fragment shift| limit
    max_step: float = 15.0          # per-iteration shift change limit
    min_step: float = 0.5           # smaller changes are suppressed (no dither)
    max_jog: float = 60.0           # largest shift difference between neighbouring fragments
    min_width: float = 0.0          # reported by mrc_check (0 = off)
    min_space: float = 0.0


@dataclass
class RuleOptions:
    bias_table: List[Tuple[float, float]] = field(default_factory=lambda: [(100.0, 0.0), (1000.0, 0.0)])
    """(space_nm, edge_bias_nm): per-edge outward bias interpolated against the free space in front of the edge."""
    serif: float = 0.0              # convex-corner serif side (0 = off); square centred on the corner
    notch: float = 0.0              # concave-corner notch side (0 = off)
    hammer_ext: float = 0.0         # line-end extension beyond the drawn end
    hammer_wing: float = 0.0        # hammerhead widening on each side of a line end
    hammer_len: float = 0.0         # hammerhead depth measured back from the end
    contact_bias: float = 0.0       # extra outward bias for small near-square features
    contact_max: float = 400.0      # features with both bbox sides <= this and aspect <= 1.5 are contacts
    max_space: float = 2000.0       # ray-cast range for space measurement


@dataclass
class ModelOptions:
    max_iter: int = 20
    gain: float = 0.6               # ~ 1/MEEF
    tol: float = 1.0                # stop when max |EPE| < tol (nm)
    search: float = 150.0
    init_from_rule: bool = False


@dataclass
class InverseOptions:
    pixel: float = 8.0
    iterations: int = 60
    step: float = 0.5
    steepness: float = 40.0         # sigmoid slope a
    gamma_binary: float = 0.02      # binarisation regulariser weight
    tv: float = 0.0                 # total-variation weight
    min_feature: float = 40.0       # drop mask islands/holes smaller than this (nm) after binarisation
    manhattan: bool = False         # return a rectilinear (pixel-run) mask instead of simplified contours
    simplify: float = 4.0           # shapely simplify tolerance (nm) for non-Manhattan output
    init: str = "target"            # 'target' | 'blurred'


@dataclass
class SRAFOptions:
    width: float = 40.0
    distance: float = 150.0         # edge-to-first-bar distance
    pitch: float = 140.0            # bar-to-bar pitch (centre to centre)
    max_bars: int = 2
    min_space: float = 420.0        # only edges with at least this free space get assists
    clearance: float = 100.0        # keep-out between a bar and other features
    inset: float = 30.0             # bar is shorter than the edge by this on each end
    min_edge: float = 120.0         # edges shorter than this get none
    min_width: float = 24.0         # printability loop never shrinks below this
    check_print: bool = True
    layer_datatype: Optional[int] = None   # datatype for assist bars (None = same as target)


@dataclass
class OPCOptions:
    stages: Tuple[str, ...] = ("model",)    # any of 'sraf', then ONE of 'rule' | 'model' | 'inverse'
    fragments: FragmentOptions = field(default_factory=FragmentOptions)
    mrc: MRCOptions = field(default_factory=MRCOptions)
    rule: RuleOptions = field(default_factory=RuleOptions)
    model: ModelOptions = field(default_factory=ModelOptions)
    inverse: InverseOptions = field(default_factory=InverseOptions)
    sraf: SRAFOptions = field(default_factory=SRAFOptions)
    progress: Optional[Callable[[str, int, float, float], Optional[bool]]] = None   # (stage, it, rms, max) -> False cancels


@dataclass
class OPCResult:
    polygons: List[Polygon]               # corrected main features
    srafs: List[Polygon]                  # assist features (empty if none)
    log: List[Dict]                       # per-iteration records {'stage','iter','rms','max'}
    epe_before: Dict[str, float]
    epe_after: Dict[str, float]
    library: object = None                # lithomask.layout.Library with cell 'OPC_TOP'
    mrc: Dict[str, float] = field(default_factory=dict)
    stage_used: str = ""

    @property
    def mask(self) -> List[Polygon]:
        return list(self.polygons) + list(self.srafs)
