@echo off
setlocal
cd /d "%~dp0"

echo Installation de MiniCam...
python -m venv .venv
if errorlevel 1 (
    echo Impossible de creer l'environnement Python.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Installation incomplete.
    pause
    exit /b 1
)

echo.
echo Installation terminee. Tu peux lancer MiniCam avec launch.bat.
pause
