@echo off
rem 실행: run.bat            이 컴퓨터에서만 접속
rem       run.bat --lan      같은 공유기의 다른 기기(노트북/폰)에서도 접속
rem       run.bat --share    인터넷 어디서나 접속 (공개 주소 발급, 비밀번호 필수)
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

if "%~1"=="--share" (
  where cloudflared >nul 2>nul
  if errorlevel 1 (
    echo 공개 주소를 만들려면 cloudflared가 필요합니다.
    echo   winget install Cloudflare.cloudflared
    exit /b 1
  )
  rem 발급되는 주소는 링크만 알면 누구나 들어온다
  if not defined CLIPPER_PASSWORD (
    echo 공개 주소는 링크만 알면 누구나 들어옵니다. 비밀번호를 정하고 다시 실행하세요.
    echo   set CLIPPER_PASSWORD=원하는비밀번호 ^&^& run.bat --share
    exit /b 1
  )
  set CLIPPER_HOST=127.0.0.1
  start /b .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port %CLIPPER_PORT%
  echo.
  echo   아래 cloudflared가 찍어주는 https 주소로 어디서나 접속할 수 있습니다.
  echo   이 창을 닫으면 주소도 함께 사라집니다.
  echo.
  cloudflared tunnel --url http://127.0.0.1:%CLIPPER_PORT%
  exit /b 0
)

.venv\Scripts\python.exe -m uvicorn app.main:app --host %CLIPPER_HOST% --port %CLIPPER_PORT%
