@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "RESTART_FLAG=--uv-bootstrap-restarted"
set "RESTARTED_AFTER_BOOTSTRAP=0"
set "BOOTSTRAP_ONLY=0"
set "SCRIPT_PATH=%~dpnx0"

if /I "%~1"=="%RESTART_FLAG%" (
    set "RESTARTED_AFTER_BOOTSTRAP=1"
    shift
)

if /I "%~1"=="--bootstrap-only" (
    set "BOOTSTRAP_ONLY=1"
    shift
)

set "UV_INSTALL_DIR=%USERPROFILE%\.local\bin"
set "UV_EXE="
set "USE_CN_MIRROR=0"
set "UV_DOWNLOAD_URL="
set "UV_PYTHON_INSTALL_MIRROR="
set "UV_DEFAULT_INDEX="
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference = 'SilentlyContinue';" ^
    "$ErrorActionPreference = 'Stop';" ^
    "$targets = @('https://astral.sh/uv/install.ps1', 'https://github.com/astral-sh/python-build-standalone/releases', 'https://pypi.org/simple/');" ^
    "foreach ($target in $targets) {" ^
    "    try {" ^
    "        Invoke-WebRequest -UseBasicParsing -Method Head -Uri $target -TimeoutSec 5 | Out-Null" ^
    "    } catch {" ^
    "        exit 1" ^
    "    }" ^
    "}" ^
    "exit 0"

if errorlevel 1 (
    set "USE_CN_MIRROR=1"
    set "UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/"
    set "UV_PYTHON_INSTALL_MIRROR=https://mirrors.ustc.edu.cn/github-release/astral-sh/python-build-standalone/"
    set "UV_DEFAULT_INDEX=https://mirrors.ustc.edu.cn/pypi/simple/"
    echo Official sources are unavailable. Using USTC mirrors...
)

if exist "%UV_INSTALL_DIR%\uv.exe" (
    set "UV_EXE=%UV_INSTALL_DIR%\uv.exe"
)

if not defined UV_EXE for /f "delims=" %%I in ('where.exe uv 2^>nul') do (
    if not defined UV_EXE (
        set "UV_EXE=%%I"
    )
)

if not defined UV_EXE (
    if "%RESTARTED_AFTER_BOOTSTRAP%"=="1" (
        echo Failed to locate uv after bootstrap.
        pause
        exit /b 1
    )

    set "UV_NO_MODIFY_PATH=1"
    set "UV_INSTALL_SCRIPT_URL=https://astral.sh/uv/install.ps1"
    set "UV_INSTALL_SCRIPT=%TEMP%\uv-installer-%RANDOM%-%RANDOM%.ps1"

    if "!USE_CN_MIRROR!"=="1" (
        set "UV_INSTALL_SCRIPT_URL=%UV_DOWNLOAD_URL%uv-installer.ps1"
    )

    if not exist "!UV_INSTALL_DIR!" (
        mkdir "!UV_INSTALL_DIR!" >nul 2>nul
    )

    echo Installing uv...
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
        "$ProgressPreference = 'SilentlyContinue';" ^
        "Invoke-WebRequest -UseBasicParsing -Uri '!UV_INSTALL_SCRIPT_URL!' -OutFile '!UV_INSTALL_SCRIPT!'"

    if errorlevel 1 (
        del /q "!UV_INSTALL_SCRIPT!" >nul 2>nul
        echo Failed to install uv.
        pause
        exit /b 1
    )

    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "!UV_INSTALL_SCRIPT!"
    set "INSTALL_EXIT=!errorlevel!"
    del /q "!UV_INSTALL_SCRIPT!" >nul 2>nul

    if not "!INSTALL_EXIT!"=="0" (
        echo Failed to install uv.
        pause
        exit /b !INSTALL_EXIT!
    )

    call "%SCRIPT_PATH%" %RESTART_FLAG% %*
    exit /b %errorlevel%
)

if /I "%RPGMT_BOOTSTRAP_ONLY%"=="1" (
    set "BOOTSTRAP_ONLY=1"
)

if "%BOOTSTRAP_ONLY%"=="1" (
    exit /b 0
)

"%UV_EXE%" run python main.py tui %*
set "EXIT_CODE=%errorlevel%"
pause
exit /b %EXIT_CODE%



