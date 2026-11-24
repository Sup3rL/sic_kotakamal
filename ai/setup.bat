@echo off
rem Setup AI Kotak Amal di Windows: buat venv + install dependency (ringan, tanpa torch).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python tidak ditemukan di PATH. Install Python 3.12+ lalu cek "Add to PATH".
    pause
    exit /b 1
)

echo [1/3] Membuat venv (.venv) ...
python -m venv .venv

echo [2/3] Upgrade pip ...
.venv\Scripts\python -m pip install --upgrade pip

echo [3/3] Install dependency ...
.venv\Scripts\python -m pip install -r requirements.txt

echo.
echo Selesai. Cara pakai:
echo   .venv\Scripts\python predict.py ..\photos\top --save hasil
echo   .venv\Scripts\python predict.py foto.jpg
pause