@echo off
setlocal
title [Cybos Plus Bridge Server]

:: Set working directory
cd /d "%~dp0"

echo -------------------------------------------------------
echo           Cybos Plus Bridge Server
echo -------------------------------------------------------
echo [TARGET] cybos_server.py
echo [PATH]   %CD%
echo [INFO] Please run as Administrator manually.
echo -------------------------------------------------------

:: Activate Virtual Environment
if exist "..\.venv32\Scripts\activate.bat" (
    echo [INFO] Activating .venv32...
    call "..\.venv32\Scripts\activate.bat"
) else (
    echo [WARN] .venv32 not found. Trying system python...
)

:: Run Server
python cybos_server.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server failed. Exit Code: %errorlevel%
    echo 1. Ensure Cybos Plus HTS is logged in as Administrator.
    echo 2. Verify that Python is 32-bit.
    echo 3. Check if pywin32, fastapi, uvicorn are installed.
)

pause
