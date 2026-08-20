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

%PY% -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo Dependencies installed successfully.
pause
endlocal
