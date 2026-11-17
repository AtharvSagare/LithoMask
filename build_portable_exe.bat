@echo off
REM Creates a single Windows application file: dist\LithoMask.exe
REM The executable embeds the application icon and does not show a console window.
cd /d %~dp0
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name LithoMask --icon "LithoMask.ico" --add-data "LITHOMASK OPENING.png;." --add-data "Lithomask dark.png;." --add-data "USER_GUIDE.md;." --add-data "examples\projects;examples\projects" lithomask_app.py
echo.
echo Portable application created: dist\LithoMask.exe
pause
