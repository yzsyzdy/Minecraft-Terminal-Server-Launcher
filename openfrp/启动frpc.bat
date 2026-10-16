@echo off
chcp 65001 >nul
title OpenFrp frpc 启动器
setlocal enabledelayedexpansion

rem ============================================================
rem  OpenFrp frpc 启动脚本
rem  使用前请修改下面的 用户Token 和 隧道ID
rem  隧道ID 支持多个, 用英文逗号分隔, 例如: 12345,67890
rem ============================================================

rem ---- 配置区: 请修改这两个值 ----
set "USER_TOKEN=在这里填写你的用户Token"
set "PROXY_IDS=在这里填写隧道ID"
rem ---------------------------------

rem ---- 可选: frpc 路径 (留空自动搜索) ----
set "FRPC_EXE="

rem ============================================================
rem  以下内容一般无需修改
rem ============================================================

rem 检查配置是否已填写
if "%USER_TOKEN%"=="在这里填写你的用户Token" (
    echo [错误] 请先在脚本中填写 USER_TOKEN
    pause
    exit /b 1
)
if "%PROXY_IDS%"=="在这里填写隧道ID" (
    echo [错误] 请先在脚本中填写 PROXY_IDS
    pause
    exit /b 1
)

rem 定位 frpc.exe
if "%FRPC_EXE%"=="" (
    rem 优先查找脚本同目录下的 openfrp\frpc.exe
    if exist "%~dp0openfrp\frpc.exe" (
        set "FRPC_EXE=%~dp0openfrp\frpc.exe"
    ) else if exist "%~dp0frpc.exe" (
        set "FRPC_EXE=%~dp0frpc.exe"
    ) else (
        rem 从 PATH 中查找
        where frpc.exe >nul 2>nul
        if not errorlevel 1 (
            set "FRPC_EXE=frpc.exe"
        )
    )
)

if "%FRPC_EXE%"=="" (
    echo [错误] 找不到 frpc.exe
    echo 请将其放到脚本同目录的 openfrp\ 文件夹下,
    echo 或修改脚本中 FRPC_EXE 变量指定完整路径
    pause
    exit /b 1
)

echo ============================================
echo   OpenFrp frpc 启动器
echo   程序: %FRPC_EXE%
echo   Token: %USER_TOKEN:~0,6%...
echo   隧道: %PROXY_IDS%
echo   按 Ctrl+C 停止
echo ============================================
echo.

rem 结束同一个 frpc.exe 的残留进程。
rem 以前是 taskkill /f /im frpc.exe —— 那会把整台机器上所有 frpc 进程一起
rem 杀掉，包括用户为别的隧道单独起的那份，以及 MSTL 面板自己管着的那份。
rem 这里先取到 frpc.exe 的完整路径，只停路径完全一致的那些进程。
set "FRPC_FULL="
for /f "delims=" %%W in ('where "%FRPC_EXE%" 2^>nul') do (
    if not defined FRPC_FULL set "FRPC_FULL=%%W"
)
if not defined FRPC_FULL set "FRPC_FULL=%FRPC_EXE%"
if defined FRPC_FULL (
    for /f "delims=" %%P in ('powershell -NoProfile -Command "Get-Process frpc -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $env:FRPC_FULL } | ForEach-Object { $_.Id }" 2^>nul') do (
        echo [提示] 停止残留进程 PID %%P
        taskkill /f /pid %%P >nul 2>nul
    )
    timeout /t 1 /nobreak >nul
)

rem 启动 frpc
rem 注意: Token 只能走命令行参数 (frpc 的 -u), 而命令行在本机对同机其它用户
rem 可见。这里是单用户桌面脚本, 风险可接受; 但值必须加引号 —— 以前没加,
rem Token 里只要出现空格或 & 就会把命令行截断, 甚至被当成第二条命令执行。
"%FRPC_EXE%" -u "%USER_TOKEN%" -p "%PROXY_IDS%"

echo.
echo [提示] frpc 已退出 (退出码: %errorlevel%)
pause
