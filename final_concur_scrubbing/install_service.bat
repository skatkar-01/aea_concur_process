@echo off
REM ============================================================
REM  install_service.bat
REM  One-time setup: registers AmExWatcher as a Windows Service
REM  using NSSM (Non-Sucking Service Manager).
REM
REM  PREREQUISITES:
REM    1. Install NSSM: https://nssm.cc/download
REM       Place nssm.exe in C:\tools\nssm\ or update NSSM_PATH below.
REM    2. Run this script as Administrator (right-click → Run as admin).
REM    3. Fill in your .env file before running.
REM ============================================================

REM ── Configure these paths ────────────────────────────────────────────────────
set SERVICE_NAME=AmExWatcher
set NSSM_PATH=C:\tools\nssm\nssm.exe
set PYTHON_EXE=C:\Users\SKatkar\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT_DIR=C:\Users\SKatkar\amex_processor
set LOG_DIR=%SCRIPT_DIR%\logs
REM ─────────────────────────────────────────────────────────────────────────────

echo.
echo  AmEx Watcher — Windows Service Installer
echo  ==========================================
echo.

REM Check NSSM exists
if not exist "%NSSM_PATH%" (
    echo  ERROR: NSSM not found at %NSSM_PATH%
    echo  Download from https://nssm.cc/download and update NSSM_PATH in this script.
    pause
    exit /b 1
)

REM Check Python exists
if not exist "%PYTHON_EXE%" (
    echo  ERROR: Python not found at %PYTHON_EXE%
    echo  Update PYTHON_EXE in this script to point to your Python installation.
    pause
    exit /b 1
)

REM Remove existing service if present
echo  Removing existing service (if any)...
"%NSSM_PATH%" stop %SERVICE_NAME% 2>nul
"%NSSM_PATH%" remove %SERVICE_NAME% confirm 2>nul

echo  Installing service...
"%NSSM_PATH%" install %SERVICE_NAME% "%PYTHON_EXE%" "watcher.py"

echo  Configuring service...
"%NSSM_PATH%" set %SERVICE_NAME% AppDirectory       "%SCRIPT_DIR%"
"%NSSM_PATH%" set %SERVICE_NAME% DisplayName        "AmEx Concur Reconciliation Watcher"
"%NSSM_PATH%" set %SERVICE_NAME% Description        "Watches Box sync folders and automatically updates the Concur tracker Excel workbook."
"%NSSM_PATH%" set %SERVICE_NAME% Start              SERVICE_AUTO_START
"%NSSM_PATH%" set %SERVICE_NAME% AppRestartDelay    5000
"%NSSM_PATH%" set %SERVICE_NAME% AppStdout          "%LOG_DIR%\service_stdout.log"
"%NSSM_PATH%" set %SERVICE_NAME% AppStderr          "%LOG_DIR%\service_stderr.log"
"%NSSM_PATH%" set %SERVICE_NAME% AppStdoutCreationDisposition 4
"%NSSM_PATH%" set %SERVICE_NAME% AppStderrCreationDisposition 4
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateFiles     1
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateOnline    1
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateSeconds   86400
"%NSSM_PATH%" set %SERVICE_NAME% AppRotateBytes     10485760

echo  Starting service...
"%NSSM_PATH%" start %SERVICE_NAME%

echo.
echo  ✓ Service installed and started.
echo.
echo  Useful commands:
echo    Check status : sc query %SERVICE_NAME%
echo    Stop         : nssm stop %SERVICE_NAME%
echo    Start        : nssm start %SERVICE_NAME%
echo    Uninstall    : nssm remove %SERVICE_NAME% confirm
echo    View logs    : type "%LOG_DIR%\amex_processor.log"
echo.
pause
