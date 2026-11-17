"""Phase 7 - verification driver: simulate the mask, measure EPE / CD maps, find hotspots, run MRC, optional process window.

    res = verify(target, mask, pm, VerifyOptions(window=WindowOptions()), name="L-shape")
    write_report(res, "out_dir")      # -> out_dir/<name>.html + PNGs

`target` = shapes that should print; `mask` = what is written (OPC result .mask incl. SRAFs, or the target itself for an uncorrected
reference). Conventions as in Phase 5: EPE > 0 = printed too large.
"""
from __future__ import annotations

import base64
import html
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
from shapely.ops import unary_union

from ..geometry import Polygon
from ..opc.common import ProcessModel, epe_stats, measure_epe
from ..opc.engine import _target_polygons
from ..optics import Grid
from ..resist import PrintResult
from .epe import CDMap, EdgeSamples, cd_map, edge_samples
from .hotspot import Hotspot, HotspotRules, find_hotspots, summarize_hotspots
from .mrc import MRCRules, Violation, run_mrc, summarize_mrc
from .window import WindowOptions, WindowResult, choose_gauges, process_window


@dataclass
class VerifyOptions:
    epe_step: float = 20.0
    corner_exclude: float = 20.0
    lineend_max: float = 220.0
    search: float = 150.0
    max_cd: float = 800.0
    hotspot: HotspotRules = field(default_factory=HotspotRules)
    mrc: MRCRules = field(default_factory=MRCRules)
    window: Optional[WindowOptions] = None          # None = skip the (slower) process window


@dataclass
class VerifyResult:
    name: str
    target: List[Polygon]
    mask: List[Polygon]
    grid: Grid
    sim: PrintResult
    samples: EdgeSamples
    epe: np.ndarray
    cdm: CDMap
    epe_stats: Dict[str, float]
    cd_stats: Dict[str, float]
    hotspots: List[Hotspot]
    mrc: List[Violation]
    window: Optional[WindowResult] = None

    @property
    def passed(self) -> bool:
        return not self.hotspots and not self.mrc


def verify(target, mask: Sequence[Polygon], pm: ProcessModel, opts: Optional[VerifyOptions] = None, name: str = "layout"
           ) -> VerifyResult:
    o = opts or VerifyOptions()
    tgt = _target_polygons(target)
    mask = list(mask)
    grid = pm.grid_for(list(tgt) + mask)
    sim = pm.simulate(mask, grid)
    S = edge_samples(tgt, o.epe_step, o.lineend_max, o.corner_exclude)
    epe = measure_epe(sim.field, sim.level, grid, S.points, S.normals, pm.inside_feature, o.search) if len(S) else np.zeros(0)
    keep = ~S.lineend
    S_run = EdgeSamples(S.points[keep], S.normals[keep], S.edge_id[keep], S.edge_len[keep], S.lineend[keep])
    feature = "dark" if pm.tone == "clear_field" else "bright"
    T = unary_union([p.to_shapely() for p in tgt])
    cdm = cd_map(S_run, T, sim.field, sim.level, grid, feature, o.max_cd, o.search)
    err = cdm.error[np.isfinite(cdm.error)]
    cds = {"n": int(len(cdm)), "n_absent": int(np.sum(~np.isfinite(cdm.printed_cd))),
           "rms": float(np.sqrt(np.mean(err ** 2))) if len(err) else 0.0, "max": float(np.max(np.abs(err))) if len(err) else 0.0,
           "mean": float(np.mean(err)) if len(err) else 0.0}
    hs = find_hotspots(tgt, sim.contours, S, epe, cdm, o.hotspot)
    mrc = run_mrc(mask, o.mrc)
    win = None
    if o.window is not None and len(cdm):
        win = process_window(mask, pm, choose_gauges(cdm, o.window.n_gauges), grid, o.window)
    return VerifyResult(name, tgt, mask, grid, sim, S, epe, cdm, epe_stats(epe), cds, hs, mrc, win)


# ======================================================================================
# Report (PNG + HTML)
# ======================================================================================
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "report"


def _ring_xy(ring):
    a = np.asarray(ring, float)
    return np.vstack([a, a[:1]])


def _draw_polys(ax, polys, **kw):
    for p in polys:
        for ring in [p.outer] + list(p.holes):
            xy = _ring_xy(ring)
            ax.plot(xy[:, 0], xy[:, 1], **kw)
            kw.pop("label", None)


def plot_overlay(res: VerifyResult, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.6))
    x0, x1, y0, y1 = res.grid.extent
    ax = axs[0]
    ax.imshow(res.sim.aerial, origin="lower", extent=res.grid.extent, cmap="gray", vmin=0)
    _draw_polys(ax, res.target, color="#00d0ff", lw=1.2, label="target")
    _draw_polys(ax, res.sim.contours, color="#ff4040", lw=1.2, label="printed")
    ax.set_title("Aerial image + target / printed contour")
    ax.legend(loc="upper right", fontsize=8)
    ax = axs[1]
    _draw_polys(ax, res.mask, color="#888888", lw=0.9, label="mask")
    _draw_polys(ax, res.target, color="#0070ff", lw=1.4, label="target")
    _draw_polys(ax, res.sim.contours, color="#e02020", lw=1.4, label="printed")
    mk = {"bridge": "X", "break": "X", "missing": "X", "pinch": "v", "lineend": "<", "extra": "s"}
    for h in res.hotspots:
        ax.plot(h.x, h.y, mk.get(h.kind, "o"), ms=11, mfc="none", mec="#ff8c00", mew=2)
        ax.annotate(h.kind, (h.x, h.y), textcoords="offset points", xytext=(6, 6), fontsize=8, color="#c06000")
    for v in res.mrc:
        ax.plot(v.x, v.y, "D", ms=7, mfc="none", mec="#9020c0", mew=1.5)
    ax.set_title(f"Overlay, {len(res.hotspots)} hotspots (orange), {len(res.mrc)} MRC (purple)")
    ax.legend(loc="upper right", fontsize=8)
    ax = axs[2]
    _draw_polys(ax, res.target, color="#bbbbbb", lw=0.8)
    if len(res.samples):
        e = np.clip(res.epe, -40, 40)
        sc = ax.scatter(res.samples.points[:, 0], res.samples.points[:, 1], c=e, cmap="coolwarm", vmin=-40, vmax=40, s=22)
        fig.colorbar(sc, ax=ax, fraction=0.046, label="EPE (nm), clipped +-40")
    ax.set_title(f"EPE map: RMS {res.epe_stats['rms']:.1f}, max {res.epe_stats['max']:.1f} nm")
    for a in axs:
        a.set_xlim(x0, x1)
        a.set_ylim(y0, y1)
        a.set_aspect("equal")
        a.set_xlabel("x (nm)")
        a.set_ylabel("y (nm)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_window(res: VerifyResult, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    w = res.window
    fig, axs = plt.subplots(1, 3, figsize=(16, 4.6))
    ax = axs[0]
    g0 = int(np.argmin([g.target_cd for g in w.gauges]))
    for fi, f in enumerate(w.defoci):
        ax.plot(w.doses, w.cd[g0, fi], marker="o", ms=3, label=f"{f:+.0f} nm")
    t = w.gauges[g0].target_cd
    ax.axhspan(t * (1 - w.cd_tol_pct / 100), t * (1 + w.cd_tol_pct / 100), color="green", alpha=0.12)
    ax.set_xlabel("relative dose")
    ax.set_ylabel("CD (nm)")
    ax.set_title(f"Bossung (smallest gauge, target {t:.0f} nm)")
    ax.legend(fontsize=7, title="defocus")
    ax = axs[1]
    ax.imshow(w.ok.astype(float), origin="lower", aspect="auto", cmap="Greens", vmin=0, vmax=1.4,
              extent=(w.doses[0], w.doses[-1], w.defoci[0], w.defoci[-1]))
    ax.set_xlabel("relative dose")
    ax.set_ylabel("defocus (nm)")
    ax.set_title(f"Passing region (all gauges within +-{w.cd_tol_pct:g} %)")
    ax = axs[2]
    ax.plot(w.defoci, w.el_pct, "o-")
    ax.axhline(w.el_min_pct, color="r", ls="--", lw=1)
    ax.set_xlabel("defocus (nm)")
    ax.set_ylabel("exposure latitude (%)")
    ax.set_title(f"EL(defocus): max {w.max_el_pct:.1f} %, DOF {w.dof_nm:.0f} nm @ EL>={w.el_min_pct:g} %")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def write_report(res: VerifyResult, outdir: str) -> Dict[str, str]:
    """Writes <outdir>/<name>_overlay.png, [<name>_window.png], <name>.html (PNGs embedded). Returns the paths."""
    os.makedirs(outdir, exist_ok=True)
    s = _slug(res.name)
    paths = {"overlay": os.path.join(outdir, f"{s}_overlay.png"), "html": os.path.join(outdir, f"{s}.html")}
    plot_overlay(res, paths["overlay"])
    if res.window is not None:
        paths["window"] = os.path.join(outdir, f"{s}_window.png")
        plot_window(res, paths["window"])
    e, c = res.epe_stats, res.cd_stats
    h = [f"<html><head><meta charset='utf-8'><title>{html.escape(res.name)}</title><style>body{{font-family:Segoe UI,Arial;margin:24px;"
         f"max-width:1250px}}table{{border-collapse:collapse}}td,th{{border:1px solid #bbb;padding:3px 9px;font-size:13px}}"
         f"th{{background:#eee}}.ok{{color:#087f23}}.bad{{color:#c62828}}</style></head><body>",
         f"<h1>Verification: {html.escape(res.name)}</h1>",
         f"<p class='{'ok' if res.passed else 'bad'}'><b>{'PASS' if res.passed else 'ATTENTION'}</b>: {len(res.hotspots)} hotspots, "
         f"{len(res.mrc)} mask-rule violations.</p>",
         "<h2>Summary</h2><table><tr><th>metric</th><th>value</th></tr>",
         f"<tr><td>EPE RMS / max / mean (nm), n={e['n']}</td><td>{e['rms']:.2f} / {e['max']:.2f} / {e['mean']:+.2f}</td></tr>",
         f"<tr><td>CD error RMS / max / mean (nm), n={c['n']}, absent={c['n_absent']}</td>"
         f"<td>{c['rms']:.2f} / {c['max']:.2f} / {c['mean']:+.2f}</td></tr>",
         f"<tr><td>Hotspots by kind</td><td>{summarize_hotspots(res.hotspots) or 'none'}</td></tr>",
         f"<tr><td>MRC violations by rule</td><td>{summarize_mrc(res.mrc) or 'none'}</td></tr>"]
    if res.window is not None:
        w = res.window
        h.append(f"<tr><td>Process window (CD +-{w.cd_tol_pct:g} %)</td><td>max EL {w.max_el_pct:.1f} %, DOF {w.dof_nm:.0f} nm "
                 f"(EL &ge; {w.el_min_pct:g} %), best focus {w.best_focus:+.0f} nm</td></tr>")
    h.append("</table>")
    h.append(f"<h2>Overlay and EPE map</h2><img src='data:image/png;base64,{_b64(paths['overlay'])}' style='max-width:100%'>")
    if res.window is not None:
        h.append(f"<h2>Process window</h2><img src='data:image/png;base64,{_b64(paths['window'])}' style='max-width:100%'>")
    if res.hotspots:
        h.append("<h2>Hotspots</h2><table><tr><th>kind</th><th>x (nm)</th><th>y (nm)</th><th>severity</th><th>detail</th></tr>")
        for k in sorted(res.hotspots, key=lambda k: (k.kind, -k.severity)):
            h.append(f"<tr><td>{k.kind}</td><td>{k.x:.0f}</td><td>{k.y:.0f}</td><td>{k.severity:.1f}</td>"
                     f"<td>{html.escape(k.detail)}</td></tr>")
        h.append("</table>")
    if res.mrc:
        h.append("<h2>Mask rule violations</h2><table><tr><th>rule</th><th>x</th><th>y</th><th>value</th><th>limit</th>"
                 "<th>detail</th></tr>")
        for v in res.mrc:
            h.append(f"<tr><td>{v.rule}</td><td>{v.x:.0f}</td><td>{v.y:.0f}</td><td>{v.value:.1f}</td><td>{v.limit:g}</td>"
                     f"<td>{html.escape(v.detail)}</td></tr>")
        h.append("</table>")
    h.append("</body></html>")
    with open(paths["html"], "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    return paths
