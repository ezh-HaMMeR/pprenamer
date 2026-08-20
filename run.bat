@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py"
) else (
    set "PY=python"
)

%PY% main.py
if errorlevel 1 (
    echo.
    echo Application failed to start.
    echo First run install_requirements.bat, then run this file again.
    echo Or run manually: %PY% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

endlocal
