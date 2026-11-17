"""Launch the editor:  python -m lithomask.gui [file.mop|file.gds]"""
import sys

from PySide6.QtCore import QElapsedTimer
from PySide6.QtWidgets import QApplication

from .splash import create_splash, finish_splash


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("LithoMask")
    app.setOrganizationName("Oppeniers")
    if sys.platform == "win32":       # own taskbar identity so the logo (not python.exe's icon) is shown
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Oppeniers.LithoMask")
        except Exception:
            pass
    from .common import app_icon
    app.setWindowIcon(app_icon())

    clock = QElapsedTimer()
    clock.start()
    splash = create_splash()          # intro image pops up immediately
    app.processEvents()

    from .mainwindow import MainWindow   # heavy imports happen while splash is visible
    w = MainWindow()
    if len(argv) > 1:
        w.open_path(argv[1])

    def _show_main():
        w.showMaximized()             # fills the desktop (respecting the taskbar) like other Windows software
        w.raise_()
        w.activateWindow()

    finish_splash(splash, clock, _show_main)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
