@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>nul
title PC Disk Workbench  -  localhost only
set "HERE=%~dp0"
set "PORT=8733"
set "URL=http://127.0.0.1:%PORT%/"

rem ---------- 1. 找 Python ----------
set "PYEXE="
if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE set "PYEXE=%%P"
  )
)
if not defined PYEXE goto NOPY

rem ---------- 2. 挑一个真正能绑定的端口 ----------
rem 8733 偶尔会落进 Windows 动态保留端口区间（bind 报 WinError 10013），
rem 这时让 server.py 自己顺延找出可用端口，省得手动改。
for /f "delims=" %%P in ('"%PYEXE%" "%HERE%server.py" --port 8733 --print-port 2^>nul') do set "PORT=%%P"
set "URL=http://127.0.0.1:%PORT%/"

rem ---------- 3. 已经在跑？直接开浏览器 ----------
"%PYEXE%" -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:%PORT%/api/health',timeout=1)" >nul 2>nul
if not errorlevel 1 (
  echo ============================================================
  echo   PC Disk Workbench
  echo   Service is ALREADY running  -  %URL%
  echo   Opening your browser...
  echo ============================================================
  start "" "%URL%"
  ping -n 4 127.0.0.1 >nul 2>nul
  exit /b 0
)

echo ============================================================
echo   PC Disk Workbench   personal disk dashboard
echo   %URL%      (local machine only)
echo ============================================================
echo   Starting... your browser will open automatically.
echo   KEEP THIS WINDOW OPEN. Close it to stop the service.
echo ------------------------------------------------------------
echo.
"%PYEXE%" "%HERE%server.py" --port %PORT%
echo.
echo Service stopped. You can close this window.
pause
exit /b 0

:NOPY
echo [ERROR] Python 3 was not found on this computer.
echo Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup, then rerun this file.
echo.
pause
exit /b 1
