# Third-party notices

LithoMask is MIT-licensed. It depends on the following open-source packages, which keep their own licences.
The Windows executables bundle these packages (built with PyInstaller).

| Package | Used for | Licence |
|---|---|---|
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) | GUI | LGPL-3.0 (Qt libraries are dynamically linked and can be replaced) |
| [NumPy](https://numpy.org/) | numerics | BSD-3-Clause |
| [SciPy](https://scipy.org/) | FFT, optimisation, linear algebra | BSD-3-Clause |
| [Shapely](https://shapely.readthedocs.io/) | geometry operations | BSD-3-Clause |
| [gdstk](https://heitzmann.github.io/gdstk/) | GDSII / OASIS I/O | BSL-1.0 |
| [pyclipper](https://github.com/greginvm/pyclipper) | polygon clipping / offsetting | MIT |
| [Matplotlib](https://matplotlib.org/) | verification and report figures | Matplotlib (PSF-based) licence |
| [PyInstaller](https://pyinstaller.org/) | building the executables | GPL-2.0 with a bootloader exception for the generated programs |

Product and format names (GDSII, OASIS, CIF, DXF, CleWin, JEOL, Layout BEAMER, GenISys) are the property of their respective
owners and are used only to describe file-format compatibility. LithoMask is not affiliated with or endorsed by them.