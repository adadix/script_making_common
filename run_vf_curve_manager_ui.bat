@echo off
REM ========================================================================
REM VF Curve Manager Tool v2.2 - Professional Launcher
REM BDC CVE Labs
REM
REM Uses %~dp0 (directory of this bat file) for all paths so the tool
REM works correctly regardless of where the project folder is placed.
REM ========================================================================
setlocal

REM --- Locate key paths relative to this bat file --------------------------
set TOOL_ROOT=%~dp0
set SRC_DIR=%~dp0src
set MAIN_SCRIPT=%~dp0src\vf_curve_manager.py

echo.
echo ========================================================================
echo   Intel VF Curve Manager Tool v2.2
echo   Professional Dashboard
echo ========================================================================
echo.
echo   Tool root : %TOOL_ROOT%
echo   Script    : %MAIN_SCRIPT%
echo.

REM --- Auto-detect Python --------------------------------------------------
set PYTHON=
REM 1. Try whatever 'python' is on the PATH first
for /f "delims=" %%I in ('where python 2^>NUL') do (
    if not defined PYTHON set PYTHON=%%I
)
REM 2. Fall back to common install locations
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
echo   Python    : %PYTHON%
echo.

REM --- Sanity check: script exists -----------------------------------------
if not exist "%MAIN_SCRIPT%" (
    echo [ERROR] vf_curve_manager.py not found at:
    echo         %MAIN_SCRIPT%
    echo.
    echo         Make sure the FULL project folder was copied, not just this bat file.
    echo         Expected structure:
    echo           ^<folder^>\run_vf_curve_manager_ui.bat  ^(this file^)
    echo           ^<folder^>\src\vf_curve_manager.py
    echo           ^<folder^>\src\vf_domains.json
    echo           ^<folder^>\src\core\
    echo           ^<folder^>\src\ui\
    echo           ^<folder^>\src\utils\
    pause
    exit /b 1
)

REM --- Check and install required Python packages --------------------------
echo [1] Checking Python dependencies...
for %%P in (numpy scipy tabulate pandas matplotlib openpyxl colorama PyQt5) do (
    "%PYTHON%" -c "import %%P" 2>NUL
    if errorlevel 1 (
        echo     Installing %%P ...
        "%PYTHON%" -m pip install %%P
    ) else (
        echo     %%P already installed
    )
)

echo.
echo [2] Launching VF Curve Manager (discovery runs automatically on every start)...
echo     ^(Fuse RAM loading takes 2-5 min on first connect -- please wait^)
echo.
cd /d "%SRC_DIR%"
"%PYTHON%" -W ignore vf_curve_manager.py %*

echo.
echo VF Curve Manager closed.
endlocal
pause
