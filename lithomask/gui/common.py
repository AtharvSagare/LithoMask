"""Units, themes and small helpers shared by the GUI modules."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

# ---- branding / persistent theme -------------------------------------------------------------------------------
DEFAULT_THEME = "light"                      # theme used the very first time the software is opened
LOGO_FILES = {                               # logo shown per theme (swap the "light" entry if a light-mode logo is added)
    "dark": "Lithomask dark.png",
    "light": "Lithomask dark.png",
}


def resource_path(name: str) -> Optional[Path]:
    """Locate a bundled file: PyInstaller bundle, next to the exe, or the project root (dev)."""
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(Path(base) / name)
    cands.append(Path(sys.executable).parent / name)
    cands.append(Path(__file__).resolve().parents[2] / name)
    for c in cands:
        if c.is_file():
            return c
    return None


def logo_pixmap(theme_name: str, height: int) -> Optional[QPixmap]:
    """Logo for the theme, scaled (HiDPI-aware) to `height` logical pixels; None if the file is missing."""
    path = resource_path(LOGO_FILES.get(theme_name, LOGO_FILES[DEFAULT_THEME]))
    if path is None:
        return None
    pm = QPixmap(str(path))
    if pm.isNull():
        return None
    app = QApplication.instance()
    dpr = max(1.0, app.primaryScreen().devicePixelRatio()) if app and app.primaryScreen() else 1.0
    out = pm.scaledToHeight(int(height * dpr), Qt.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def app_icon() -> QIcon:
    path = resource_path(LOGO_FILES["dark"])
    return QIcon(str(path)) if path else QIcon()


def _settings_enabled() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"     # tests / headless never touch saved settings


def load_theme() -> str:
    """Last theme the user chose; light the first time."""
    if not _settings_enabled():
        return DEFAULT_THEME
    name = QSettings("Oppeniers", "LithoMask").value("theme", DEFAULT_THEME)
    return name if name in ("light", "dark") else DEFAULT_THEME


def save_theme(name: str) -> None:
    if _settings_enabled():
        s = QSettings("Oppeniers", "LithoMask")
        s.setValue("theme", name)
        s.sync()

UNITS = {"um": 1000.0, "nm": 1.0, "mm": 1.0e6, "cm": 1.0e7}   # nm per displayed unit
_DECIMALS = {"nm": 3, "um": 4, "mm": 6, "cm": 7}
_ALIASES = {"nm": "nm", "um": "um", "µm": "um", "μm": "um", "micron": "um", "mm": "mm", "cm": "cm", "m": "m", "in": "in",
            "inch": "in", '"': "in"}
_NM_PER = {"nm": 1.0, "um": 1.0e3, "mm": 1.0e6, "cm": 1.0e7, "m": 1.0e9, "in": 2.54e7}


def decimals(unit: str) -> int:
    """Spin-box decimals that still resolve 1 nm in `unit`."""
    return _DECIMALS.get(unit, 4)


def fmt_auto(nm: float) -> str:
    """Length in nm as text with the most readable unit: nm, um, mm or cm (e.g. '12.5 mm')."""
    a = abs(nm)
    if a >= 1.0e7:
        u, k = "cm", 1.0e7
    elif a >= 1.0e6:
        u, k = "mm", 1.0e6
    elif a >= 1.0e3:
        u, k = "um", 1.0e3
    else:
        u, k = "nm", 1.0
    s = f"{nm / k:.4f}".rstrip("0").rstrip(".")
    return f"{'0' if s in ('-0', '') else s} {u}"


def parse_length(text: str, default_unit: str = "nm"):
    """'250', '250 nm', '1um', '2.5 mm', '1 cm', '2 inch' -> nm (float); None if it cannot be read."""
    import re
    m = re.fullmatch(r'\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([^\d\s]*)\s*', text or "")
    if not m:
        return None
    u = _ALIASES.get(m.group(2).lower() if m.group(2) else default_unit)
    if u is None:
        return None
    return float(m.group(1)) * _NM_PER[u]


def to_nm(value: float, unit: str) -> float:
    return value * UNITS[unit]


def from_nm(nm: float, unit: str) -> float:
    return nm / UNITS[unit]


def fmt(nm: float, unit: str = "um") -> str:
    """Format a length given in nm for display in `unit` (trailing zeros trimmed)."""
    s = f"{nm / UNITS[unit]:.{decimals(unit)}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class Theme:
    name: str
    bg: str
    grid_minor: str
    grid_major: str
    axis: str
    text: str
    ruler_bg: str
    selection: str
    preview: str
    ref_alpha: int


THEMES = {
    "dark": Theme("dark", "#1b1d22", "#272a31", "#353944", "#6a6f7d", "#c8ccd6", "#23262d", "#ffd54a", "#7ee0ff", 45),
    "light": Theme("light", "#f6f7f9", "#e4e6eb", "#cfd3dc", "#8a8f9c", "#2b2f38", "#e9ebf0", "#d84315", "#0277bd", 60),
}


def apply_theme(app: QApplication, name: str) -> Theme:
    t = THEMES[name]
    app.setStyle("Fusion")
    p = QPalette()
    if name == "dark":
        base, alt, text, btn = QColor("#23262d"), QColor("#2b2f38"), QColor("#d6d9e0"), QColor("#2f333d")
        p.setColor(QPalette.Highlight, QColor("#3d6fd1"))
    else:
        base, alt, text, btn = QColor("#ffffff"), QColor("#eef0f4"), QColor("#20232a"), QColor("#e6e8ee")
        p.setColor(QPalette.Highlight, QColor("#2f6fe0"))
    p.setColor(QPalette.Window, btn)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, btn)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.PlaceholderText, QColor("#8a8f9c"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#7d818c"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#7d818c"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#7d818c"))
    app.setPalette(p)
    return t
