"""Phase 6 - calibration gauges: containers and CSV import / export.

A *gauge* is one wafer measurement tied to one mask pattern:

  kind   meaning of `measured`                                         simulated quantity
  -----  ------------------------------------------------------------  ----------------------------------------------
  CD     width (nm) of the printed resist FEATURE crossing the point   measure_cd on a cutline through (x, y)
  SPACE  width (nm) of the SPACE (non-feature) crossing the point      same, opposite polarity
  EP     signed distance (nm) from (x, y) to the printed contour       edge_placement along `direction` from (x, y)
         measured along `direction` (positive = along the direction)

Conventions
  * x, y in nm, in the coordinate frame of the mask pattern `pattern` (the same frame as the polygons you pass in).
  * direction = angle in degrees, counter-clockwise from +x (CD / SPACE: orientation of the cutline; EP: direction in which
    the signed distance is counted, normally the outward normal of the intended edge).
  * "feature" = what stays on the wafer: dark region of the aerial image for clear_field masks, bright for dark_field
    (same definition as lithomask.opc.common.ProcessModel).
  * weight >= 0; weight 0 keeps the gauge in the report but removes it from the cost.
  * span (nm, optional): CD / SPACE -> cutline length (default max(600, 3 * |measured|)); EP -> full search range, the contour
    is searched within +- span/2 of (x, y) (default 300).

CSV format (UTF-8, header required, '#' comment lines allowed).  Required columns (header names are case-insensitive, spaces /
hyphens / underscores are equivalent, the aliases in brackets are accepted):
  pattern id [pattern, pattern_id] | type [kind] (CD, SPACE, EP) | x [x_nm] | y [y_nm] | direction [dir, angle]
  (degrees, or x / h / horizontal = 0, y / v / vertical = 90) | measured value [measured, value, measured_nm] |
  weight [wt] (optional, default 1)
Optional columns: span, id (free label).
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

import numpy as np

KINDS = ("CD", "SPACE", "EP")
DEFAULT_EP_SPAN = 300.0
DEFAULT_MIN_CUT_SPAN = 600.0

_KIND_ALIASES = {"cd": "CD", "line": "CD", "width": "CD", "linewidth": "CD",
                 "space": "SPACE", "sp": "SPACE", "gap": "SPACE",
                 "ep": "EP", "epe": "EP", "edge": "EP", "edge_placement": "EP", "edgeplacement": "EP"}
_DIR_ALIASES = {"x": 0.0, "h": 0.0, "horizontal": 0.0, "y": 90.0, "v": 90.0, "vertical": 90.0}
_COLUMN_ALIASES = {
    "pattern": "pattern", "pattern_id": "pattern", "patternid": "pattern", "pattern_name": "pattern",
    "type": "kind", "kind": "kind", "gauge_type": "kind",
    "x": "x", "x_nm": "x", "y": "y", "y_nm": "y",
    "direction": "direction", "dir": "direction", "angle": "direction", "direction_deg": "direction",
    "measured_value": "measured", "measured": "measured", "value": "measured", "measured_nm": "measured",
    "weight": "weight", "wt": "weight", "span": "span", "span_nm": "span", "id": "id", "gauge_id": "id", "label": "id",
}
_REQUIRED = ("pattern", "kind", "x", "y", "direction", "measured")


def normalize_kind(kind: str) -> str:
    k = str(kind).strip().lower().replace("-", "_").replace(" ", "_")
    if k in _KIND_ALIASES:
        return _KIND_ALIASES[k]
    if k.upper() in KINDS:
        return k.upper()
    raise ValueError(f"unknown gauge type {kind!r}; expected one of {KINDS}")


def parse_direction(value) -> float:
    """Degrees (number or numeric string) or one of x/h/horizontal (0), y/v/vertical (90)."""
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _DIR_ALIASES:
            return _DIR_ALIASES[s]
        return float(s)
    return float(value)


@dataclass
class Gauge:
    pattern: str
    kind: str
    x: float
    y: float
    direction: float = 0.0
    measured: float = 0.0
    weight: float = 1.0
    span: Optional[float] = None
    id: str = ""

    def __post_init__(self):
        self.kind = normalize_kind(self.kind)
        self.pattern = str(self.pattern)
        for name in ("x", "y", "direction", "measured", "weight"):
            v = float(getattr(self, name))
            if not math.isfinite(v):
                raise ValueError(f"gauge {self.label()}: {name} must be finite, got {v}")
            setattr(self, name, v)
        if self.weight < 0:
            raise ValueError(f"gauge {self.label()}: weight must be >= 0")
        if self.span is not None:
            self.span = float(self.span)
            if not self.span > 0:
                raise ValueError(f"gauge {self.label()}: span must be > 0")

    def label(self) -> str:
        return self.id or f"{self.pattern}/{self.kind}@({self.x:g},{self.y:g})"

    @property
    def unit(self) -> np.ndarray:
        a = math.radians(self.direction)
        return np.array([math.cos(a), math.sin(a)])

    def cut_span(self) -> float:
        """Cutline length (CD / SPACE) or full search range (EP) in nm."""
        if self.span is not None:
            return self.span
        if self.kind == "EP":
            return DEFAULT_EP_SPAN
        return max(DEFAULT_MIN_CUT_SPAN, 3.0 * abs(self.measured))

    def with_measured(self, measured: float) -> "Gauge":
        return Gauge(self.pattern, self.kind, self.x, self.y, self.direction, float(measured), self.weight, self.span, self.id)


@dataclass
class GaugeSet:
    gauges: List[Gauge] = field(default_factory=list)

    # ---------------------------------------------------------------- container protocol
    def __len__(self) -> int:
        return len(self.gauges)

    def __iter__(self) -> Iterator[Gauge]:
        return iter(self.gauges)

    def __getitem__(self, i):
        return self.gauges[i]

    def add(self, *items: Gauge) -> "GaugeSet":
        self.gauges.extend(items)
        return self

    def by_kind(self, kind: str) -> List[Gauge]:
        k = normalize_kind(kind)
        return [g for g in self.gauges if g.kind == k]

    def pattern_ids(self) -> List[str]:
        """Pattern ids in order of first appearance."""
        return list(dict.fromkeys(g.pattern for g in self.gauges))

    def counts(self) -> Dict[str, int]:
        return {k: len(self.by_kind(k)) for k in KINDS}

    def weights(self) -> np.ndarray:
        return np.array([g.weight for g in self.gauges], float)

    def measured(self) -> np.ndarray:
        return np.array([g.measured for g in self.gauges], float)

    # ---------------------------------------------------------------- CSV
    @staticmethod
    def from_csv(path_or_text) -> "GaugeSet":
        """Read gauges from a CSV file path (or from CSV text containing a newline). Raises ValueError with the line number."""
        if isinstance(path_or_text, str) and "\n" in path_or_text:
            name = "<text>"
            raw = path_or_text
        else:
            name = str(path_or_text)
            with open(path_or_text, "r", encoding="utf-8-sig", newline="") as fh:
                raw = fh.read()
        lines = [(i + 1, ln) for i, ln in enumerate(raw.splitlines())]
        lines = [(n, ln) for n, ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if not lines:
            raise ValueError(f"{name}: empty gauge file")
        header_no, header_line = lines[0]
        delim = ";" if header_line.count(";") > header_line.count(",") else ("\t" if header_line.count("\t") > header_line.count(",") else ",")
        rows = list(csv.reader([ln for _, ln in lines], delimiter=delim))
        cols: List[Optional[str]] = []
        for h in rows[0]:
            key = h.strip().lower().replace("-", "_").replace(" ", "_")
            cols.append(_COLUMN_ALIASES.get(key))
        missing = [c for c in _REQUIRED if c not in cols]
        if missing:
            raise ValueError(f"{name}: line {header_no}: missing required column(s) {missing}; header was {rows[0]}")
        out = GaugeSet()
        for (lineno, _), row in zip(lines[1:], rows[1:]):
            rec: Dict[str, str] = {}
            for c, v in zip(cols, row):
                if c is not None:
                    rec[c] = v.strip()
            try:
                if any(rec.get(c, "") == "" for c in _REQUIRED):
                    raise ValueError("empty required field")
                out.gauges.append(Gauge(
                    pattern=rec["pattern"], kind=rec["kind"], x=float(rec["x"]), y=float(rec["y"]),
                    direction=parse_direction(rec["direction"]), measured=float(rec["measured"]),
                    weight=float(rec["weight"]) if rec.get("weight", "") != "" else 1.0,
                    span=float(rec["span"]) if rec.get("span", "") != "" else None,
                    id=rec.get("id", "")))
            except ValueError as e:
                raise ValueError(f"{name}: line {lineno}: {e}") from None
        return out

    def to_csv(self, path: Optional[str] = None) -> str:
        """Write the canonical CSV (all columns); returns the text and writes it to `path` if given."""
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["pattern id", "type", "x", "y", "direction", "measured value", "weight", "span", "id"])
        for g in self.gauges:
            w.writerow([g.pattern, g.kind, f"{g.x:.6g}", f"{g.y:.6g}", f"{g.direction:.6g}", f"{g.measured:.9g}",
                        f"{g.weight:.6g}", "" if g.span is None else f"{g.span:.6g}", g.id])
        text = buf.getvalue()
        if path is not None:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        return text
