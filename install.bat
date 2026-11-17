@echo off
REM LithoMask installer: creates .venv and installs dependencies (Python 3.10+ required)
cd /d %~dp0
if not exist .venv (python -m venv .venv || (echo Python 3.10+ not found & exit /b 1))
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || exit /b 1
python -c "import lithomask.workflow, lithomask.gui.mainwindow; print('LithoMask install OK')"
echo Start with run_lithomask.bat
pause
