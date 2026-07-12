@echo off
chcp 65001 >nul
title MSTL Build

echo ============================================
echo  MSTL - Build Executable
echo ============================================
echo.

python -m nuitka --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [setup] Installing Nuitka ...
    pip install nuitka
    if %ERRORLEVEL% neq 0 (
        echo [error] Nuitka install failed.
        pause
        exit /b 1
    )
)

where gcc >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [info] MinGW not found, will use MSVC mode.
    echo        Requires Visual Studio Build Tools or Windows SDK.
    echo.
    set "MINGW_FLAG="
) else (
    set "MINGW_FLAG=--mingw64"
)

if exist build rmdir /s /q build >nul 2>&1

echo [build] Compiling start.py -^> build\MSTL.exe ...
echo.

python -m nuitka ^
    --standalone ^
    --assume-yes-for-downloads ^
    --enable-plugin=anti-bloat ^
    --noinclude-setup-mode=noautomode ^
    --windows-console-mode ^
    --windows-icon-from-ico=icon.png ^
    --output-dir=build ^
    --product-name=MSTL ^
    --file-description="Minecraft Terminal Server Launcher" ^
    --copyright="MSTL" ^
    start.py

if %ERRORLEVEL% equ 0 (
    echo.
    echo ============================================
    echo  [success] Build complete!
    echo.
    echo  Output: build\MSTL.exe
    echo.
    echo  To use: copy build\ folder anywhere and run MSTL.exe.
    echo  First run creates servers/ and config.json automatically.
    echo ============================================
) else (
    echo.
    echo [error] Build failed. Check output above.
    echo.
    echo  Common causes:
    echo    - Missing C compiler (install MSVC Build Tools or TDM-GCC)
    echo    - Python version incompatible (Python 3.10+ recommended)
    echo    - Insufficient disk space
)

echo.
pause
