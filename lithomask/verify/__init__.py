"""Phase 7 - verification and signoff. Entry point: lithomask.verify.verify(target, mask, process_model, options) + write_report."""
from .core import VerifyOptions, VerifyResult, plot_overlay, plot_window, verify, write_report
from .epe import CDMap, EdgeSamples, cd_map, edge_samples
from .hotspot import Hotspot, HotspotRules, find_hotspots, summarize_hotspots
from .mrc import MRCRules, Violation, run_mrc, summarize_mrc
from .window import Gauge, WindowOptions, WindowResult, choose_gauges, process_window

__all__ = ["verify", "write_report", "VerifyOptions", "VerifyResult", "plot_overlay", "plot_window", "CDMap", "EdgeSamples",
           "cd_map", "edge_samples", "Hotspot", "HotspotRules", "find_hotspots", "summarize_hotspots", "MRCRules", "Violation",
           "run_mrc", "summarize_mrc", "Gauge", "WindowOptions", "WindowResult", "choose_gauges", "process_window"]
