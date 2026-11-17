@echo off
REM Builds the single-file Windows executable dist\LithoMask.exe from LithoMask.spec (PyInstaller).
REM The spec also replaces the old Visual C++ runtime that PyInstaller would otherwise bundle, which
REM caused "DLL load failed while importing QtCore" on some systems.
cd /d %~dp0
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean LithoMask.spec
echo.
echo Result: dist\LithoMask.exe
pause