@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting debug run... > debug_console.log
py main.py >> debug_console.log 2>&1
echo. >> debug_console.log
echo Exit code: %ERRORLEVEL% >> debug_console.log
type debug_console.log
echo.
pause
