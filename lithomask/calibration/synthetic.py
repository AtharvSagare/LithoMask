"""Phase 6 - synthetic calibration data (for tests, demos and for trying the workflow before real wafer data exist).

standard_patterns(cd) builds test structures for a clear-field mask (chrome lines print as dark resist lines):
  'dense_<pitch>' / 'iso_<pitch>'   vertical lines (length 1500 nm) at the given pitch, centred on x = 0; gauges: CD of the centre
                                    line, SPACE to its right neighbour (if any), EP of its right edge
  'lineend'                         one line, length 1200 nm; gauge: EP of the line end (pull-back)
synthesize_measurements() simulates the template gauges with a known 'truth' parameter vector and (optionally) adds Gaussian noise
-> a GaugeSet that looks like measured data; the calibration should then recover the truth.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .. import geometry as g
from ..geometry import Polygon
from .gauges import Gauge, GaugeSet
from .model import CalibrationProblem


def line_array(cd: float, pitch: float, n_lines: int, length: float = 1500.0, layer: int = 1) -> List[Polygon]:
    xs = (np.arange(n_lines) - (n_lines - 1) / 2.0) * pitch
    return [g.rectangle(x - cd / 2, -length / 2, x + cd / 2, length / 2, layer) for x in xs]


def standard_patterns(cd: float = 180.0, pitches: Sequence[float] = (360.0, 450.0, 600.0, 900.0, 1500.0),
                      lineend_length: float = 1200.0) -> Tuple[Dict[str, List[Polygon]], GaugeSet]:
    """(patterns, template gauges with measured = 0). Pitches >= 1500 nm are treated as isolated lines (no SPACE gauge)."""
    pats: Dict[str, List[Polygon]] = {}
    gs = GaugeSet()
    for p in pitches:
        dense = p < 1500.0
        n = 1 if not dense else (7 if p <= 400 else 5 if p <= 600 else 3)
        pid = f"dense_{int(p)}" if dense else f"iso_{int(p)}"
        pats[pid] = line_array(cd, p, n)
        gs.add(Gauge(pid, "CD", 0.0, 0.0, 0.0, 0.0, 1.0, id=f"{pid}/CD"))
        gs.add(Gauge(pid, "EP", cd / 2, 0.0, 0.0, 0.0, 1.0, id=f"{pid}/EP"))
        if dense and n > 1:
            gs.add(Gauge(pid, "SPACE", p / 2, 0.0, 0.0, 0.0, 1.0, span=max(600.0, 1.3 * p), id=f"{pid}/SP"))
    pats["lineend"] = [g.rectangle(-cd / 2, -lineend_length / 2, cd / 2, lineend_length / 2, 1)]
    gs.add(Gauge("lineend", "EP", 0.0, lineend_length / 2, 90.0, 0.0, 1.0, span=300.0, id="lineend/EP"))
    return pats, gs


def synthesize_measurements(problem: CalibrationProblem, truth: Mapping[str, float], noise_nm: float = 0.0,
                            seed: int = 1234, weights: Optional[Mapping[str, float]] = None) -> GaugeSet:
    """New GaugeSet: the problem's gauges re-measured with `truth` parameter values (name -> value; unspecified parameters keep
    the problem's current values) plus Gaussian noise (sigma noise_nm). Gauges whose measurement fails for the truth are dropped.
    weights: optional {kind: weight} to override the template weights (e.g. {'SPACE': 2.0})."""
    vals = {**problem.params.as_dict(), **dict(truth)}
    full = np.array([vals[n] for n in problem.params.names], float)
    ev = problem.evaluate(full)
    rng = np.random.default_rng(seed)
    out = GaugeSet()
    for i, gg in enumerate(problem.gauges):
        if ev.failed[i]:
            continue
        m = float(ev.simulated[i]) + (rng.normal(0.0, noise_nm) if noise_nm > 0 else 0.0)
        ng = gg.with_measured(m)
        if weights and gg.kind in weights:
            ng.weight = float(weights[gg.kind])
        out.add(ng)
    return out
