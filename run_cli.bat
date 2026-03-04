@echo off
REM ========================================================================
REM VF Curve Manager Tool v2.2 - CLI Launcher
REM BDC CVE Labs
REM
REM Uses %~dp0 (directory of this bat file) for all paths so the tool
REM works correctly regardless of where the project folder is placed.
REM ========================================================================
setlocal

set SRC_DIR=%~dp0src
set CLI_SCRIPT=%~dp0src\vf_curve_manager_cli.py

echo ========================================
echo VF Curve Manager v2.2 - CLI Mode
echo ========================================
echo.

REM --- Auto-detect Python --------------------------------------------------
set PYTHON=
for /f "delims=" %%I in ('where python 2^>NUL') do (
    if not defined PYTHON set PYTHON=%%I
)
if not defined PYTHON (
    for %%P in (
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
        "C:\Python39\python.exe"
        "C:\Python38\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    ) do (
        if not defined PYTHON (
            if exist %%P set PYTHON=%%~P
        )
    )
)
if not defined PYTHON (
    echo [ERROR] Python not found on PATH or in common install locations.
    echo         Please install Python 3.10+ and ensure it is added to PATH.
    pause
    exit /b 1
)
echo   Python : %PYTHON%
echo.

REM --- Sanity check: script exists -----------------------------------------
if not exist "%CLI_SCRIPT%" (
    echo [ERROR] vf_curve_manager_cli.py not found at:
    echo         %CLI_SCRIPT%
    echo.
    echo         Make sure the FULL project folder was copied, not just this bat file.
    pause
    exit /b 1
)

REM --- No command: drop into interactive shell in src/ ---------------------
if "%1"=="" (
    echo No command specified.  Opening interactive shell in src\  ...
    echo.
    echo Usage: run_cli.bat [command] [options]
    echo.
    echo Available commands:
    echo   list, show, bump, edit, flatten, customize, dump-registers
    echo   sweep, revert-last
    echo.
    echo Global flags  ^(before the command^):
    echo   --mock          Run without hardware ^(uses discovery cache^)
    echo   --no-sut-check  Skip SUT voltage verification after write
    echo   --json          Machine-readable JSON output
    echo   --rediscover    Force a full VF register re-scan
    echo.
    echo Examples:
    echo   run_cli.bat list
    echo   run_cli.bat show --domains cluster0_bigcore ring
    echo   run_cli.bat bump --domains ring --value 10 --direction up
    echo   run_cli.bat sweep --domains ring --from -50 --to 50 --step 10
    echo   run_cli.bat revert-last
    echo   run_cli.bat --mock list
    echo.
    cd /d "%SRC_DIR%"
    cmd /k
    exit /b 0
)

REM --- Run CLI -------------------------------------------------------------
cd /d "%SRC_DIR%"
"%PYTHON%" -W ignore vf_curve_manager_cli.py %*
cd /d "%~dp0"
endlocal
