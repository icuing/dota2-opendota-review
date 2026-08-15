@echo off
setlocal EnableExtensions
set "EXIT_CODE=1"
set "PYTHON_CMD="

where py.exe >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python.exe >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo Python 3 was not found.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/windows/
    echo During installation, select "Add Python to PATH".
    goto :finish
)

%PYTHON_CMD% "%~dp0dota2_review.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

:finish
echo.
pause
exit /b %EXIT_CODE%
