# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for LithoMask - builds the single-file Windows executable dist\LithoMask.exe
# All paths are relative to this file, so the build works from any clone of the repository.
import os
import PySide6
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = SPECPATH  # directory containing this spec file


def p(*parts):
    return os.path.join(ROOT, *parts)


datas = [
    (p('LITHOMASK OPENING.png'), '.'),
    (p('Lithomask dark.png'), '.'),
    (p('USER_GUIDE.md'), '.'),
    (p('examples', 'projects'), 'examples/projects'),
]
hiddenimports = ['pyclipper', 'scipy.special._ufuncs_cxx']
datas += collect_data_files('shapely')
hiddenimports += collect_submodules('lithomask')
hiddenimports += collect_submodules('gdstk')


a = Analysis(
    [p('lithomask_app.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'pytest', 'numba', 'llvmlite', 'fsspec', 'pandas', 'torch', 'tensorflow',
              'sklearn', 'IPython', 'jupyter', 'notebook', 'matplotlib.tests', 'sympy', 'dask', 'h5py', 'cv2',
              'PIL.ImageQt'],
    noarchive=False,
    optimize=0,
)

# --- FIX for "ImportError: DLL load failed while importing QtCore" -------------------------------------
# PyInstaller copies the Visual C++ runtime of the *build* Python (e.g. C:\Program Files\Python310, v14.29)
# to the root of the exe. That old copy shadows the newer runtime that Qt 6 needs. Drop the root-level copies
# and ship the newer ones that come with PySide6 instead.
_pyside_dir = os.path.dirname(PySide6.__file__)
_old_rt = {'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll'}
a.binaries = [b for b in a.binaries
              if not (os.path.dirname(b[0]) == '' and os.path.basename(b[0]).lower() in _old_rt)]
for _n in ('vcruntime140.dll', 'vcruntime140_1.dll'):
    a.binaries.append((_n, os.path.join(_pyside_dir, _n), 'BINARY'))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LithoMask',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can corrupt Qt / runtime DLLs
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[p('LithoMask.ico')],
)