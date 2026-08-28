@echo off
rem 실행: run.bat          이 컴퓨터에서만 접속
rem       run.bat --lan    같은 공유기의 다른 기기(노트북/폰)에서도 접속
setlocal
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg이 필요합니다. 설치 후 다시 실행하세요.
  echo   winget install Gyan.FFmpeg
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo python이 필요합니다. https://www.python.org/downloads/ 에서 설치하세요.
  echo   설치할 때 "Add python.exe to PATH"를 반드시 체크하세요.
  exit /b 1
)

if not exist .venv (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -q --upgrade pip
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 exit /b 1

if not defined CLIPPER_HOST set CLIPPER_HOST=127.0.0.1
if not defined CLIPPER_PORT set CLIPPER_PORT=8000
if "%~1"=="--lan" set CLIPPER_HOST=0.0.0.0

.venv\Scripts\python.exe -m uvicorn app.main:app --host %CLIPPER_HOST% --port %CLIPPER_PORT%
