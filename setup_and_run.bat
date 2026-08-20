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

%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies.
    pause
    exit /b 1
)

%PY% main.py
if errorlevel 1 (
    echo.
    echo Application failed to start.
    pause
    exit /b 1
)

endlocal
