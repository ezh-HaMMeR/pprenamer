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
if errorlevel 1 pause & exit /b 1

%PY% -m pip install -r requirements.txt
if errorlevel 1 pause & exit /b 1

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

del /q PPRenamer.spec 2>nul

%PY% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --noconsole ^
  --onefile ^
  --name "PPRenamer" ^
  --icon "app.ico" ^
  --add-data "app.ico;." ^
  main.py

if errorlevel 1 pause & exit /b 1

echo.
echo Build completed.
echo EXE path: dist\PPRenamer.exe
echo.
pause
endlocal
