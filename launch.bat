@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\pythonw.exe" minicam.py
) else (
    echo MiniCam n'est pas encore installe.
    echo Lance install.bat une premiere fois, puis relance MiniCam.vbs.
    pause
)
