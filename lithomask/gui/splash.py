"""Start-up splash (intro) screen for LithoMask: shows 'LITHOMASK OPENING.png'
centred on screen (not full screen), fades in, holds, fades out."""
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QPropertyAnimation, QTimer, QEasingCurve
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QSplashScreen

SPLASH_FILE = "LITHOMASK OPENING.png"
SPLASH_WIDTH = 760      # px on screen (scaled, aspect kept; never > 60% of screen width)
HOLD_MS = 6000          # minimum time the splash stays fully visible
FADE_IN_MS = 0
FADE_OUT_MS = 0


def _find_image():
    cands = []
    base = getattr(sys, "_MEIPASS", None)          # PyInstaller bundle
    if base:
        cands.append(Path(base) / SPLASH_FILE)
    cands.append(Path(sys.executable).parent / SPLASH_FILE)
    cands.append(Path(__file__).resolve().parents[2] / SPLASH_FILE)   # project root (dev)
    for c in cands:
        if c.is_file():
            return c
    return None


def splash_enabled() -> bool:
    if os.environ.get("LITHOMASK_NO_SPLASH"):
        return False
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":   # tests / headless
        return False
    return True


class IntroSplash(QSplashScreen):
    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setWindowOpacity(0.0)
        self._anim = None

    def _fade(self, start, end, ms, on_done=None):
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(ms)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        if on_done:
            self._anim.finished.connect(on_done)
        self._anim.start()

    def fade_in(self):
        self._fade(0.0, 1.0, FADE_IN_MS)

    def fade_out(self, on_done):
        self._fade(self.windowOpacity(), 0.0, FADE_OUT_MS, on_done)


def create_splash():
    """Build + show the splash. Returns it, or None (disabled / image missing)."""
    if not splash_enabled():
        return None
    path = _find_image()
    if path is None:
        return None
    pm = QPixmap(str(path))
    if pm.isNull():
        return None
    screen = QGuiApplication.primaryScreen()
    max_w = int(screen.availableGeometry().width() * 0.6) if screen else SPLASH_WIDTH
    pm = pm.scaledToWidth(min(SPLASH_WIDTH, max_w), Qt.SmoothTransformation)
    sp = IntroSplash(pm)
    if screen:                                        # centre on the screen
        sp.move(screen.availableGeometry().center() - sp.rect().center())
    sp.show()
    sp.fade_in()
    return sp


def finish_splash(splash, started_ms_timer, then):
    """Keep the splash up for at least HOLD_MS total, fade it out, then call then()."""
    if splash is None:
        then()
        return
    remaining = max(0, HOLD_MS - started_ms_timer.elapsed())

    def _out():
        splash.fade_out(lambda: (splash.close(), then()))

    QTimer.singleShot(remaining, _out)
