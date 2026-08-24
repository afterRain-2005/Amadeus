@echo off
setlocal EnableExtensions
chcp 65001 >nul

cd /d "%~dp0"
set "AMADEUS_ROOT=%CD%"
set "DESKTOP_DIR=%AMADEUS_ROOT%\apps\desktop-tauri"
set "RELEASE_EXE=%AMADEUS_ROOT%\target\release\amadeus-desktop.exe"
set "DEBUG_EXE=%AMADEUS_ROOT%\target\debug\amadeus-desktop.exe"

if /I "%~1"=="--help" goto :help
if /I "%~1"=="--dev" goto :dev
if /I "%~1"=="--build" goto :build
if /I "%~1"=="--console" goto :console
if not "%~1"=="" goto :usage_error

rem Prefer an installed native build, then a local release/debug build.
if exist "%LOCALAPPDATA%\Amadeus Next\amadeus-desktop.exe" (
    start "" "%LOCALAPPDATA%\Amadeus Next\amadeus-desktop.exe"
    exit /b 0
)
if exist "%ProgramFiles%\Amadeus Next\amadeus-desktop.exe" (
    start "" "%ProgramFiles%\Amadeus Next\amadeus-desktop.exe"
    exit /b 0
)
if exist "%RELEASE_EXE%" (
    start "" "%RELEASE_EXE%"
    exit /b 0
)
if exist "%DEBUG_EXE%" (
    start "" "%DEBUG_EXE%"
    exit /b 0
)

echo [Amadeus] Native executable not found. Starting the Tauri development build...
goto :dev

:console
if exist "%RELEASE_EXE%" (
    "%RELEASE_EXE%"
    exit /b %errorlevel%
)
if exist "%DEBUG_EXE%" (
    "%DEBUG_EXE%"
    exit /b %errorlevel%
)
echo [Amadeus] No native executable has been built yet.
goto :dev

:dev
call :check_tools
if errorlevel 1 goto :failed
echo [Amadeus] Starting native Tauri development mode...
call pnpm --dir "%DESKTOP_DIR%" tauri dev
exit /b %errorlevel%

:build
call :check_tools
if errorlevel 1 goto :failed
echo [Amadeus] Building native release installers (MSI + NSIS)...
call pnpm --dir "%DESKTOP_DIR%" tauri build
if errorlevel 1 goto :build_failed
echo.
echo [Amadeus] Build complete:
echo   %AMADEUS_ROOT%\target\release\bundle
exit /b 0

:check_tools
where cargo >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Rust/Cargo was not found. Install it from https://rustup.rs/
    exit /b 1
)
where pnpm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pnpm was not found. Install Node.js, then run: corepack enable
    exit /b 1
)
if not exist "%DESKTOP_DIR%\package.json" (
    echo [ERROR] Tauri project is missing: %DESKTOP_DIR%
    exit /b 1
)
exit /b 0

:usage_error
echo [ERROR] Unknown argument: %~1
goto :help_failed

:build_failed
echo.
echo [ERROR] Native release build failed. Review the output above.
goto :failed

:help
echo Usage:
echo   start.bat             Start installed/local native Amadeus; dev fallback
echo   start.bat --dev       Run the Tauri development build
echo   start.bat --console   Run an existing native binary in this console
echo   start.bat --build     Build MSI and NSIS release installers
echo   start.bat --help      Show this help
exit /b 0

:help_failed
call :help
exit /b 2

:failed
echo.
pause
exit /b 1
