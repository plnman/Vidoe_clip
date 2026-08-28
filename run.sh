#!/usr/bin/env bash
# 실행: ./run.sh            이 컴퓨터에서만 접속
#       ./run.sh --lan      같은 공유기의 다른 기기(노트북·폰)에서도 접속
#       ./run.sh --share    인터넷 어디서나 접속 (공개 주소 발급, 비밀번호 필수)
# 처음 실행하면 가상환경을 만들고 의존성을 설치한다.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-}"
export CLIPPER_HOST="${CLIPPER_HOST:-127.0.0.1}"
export CLIPPER_PORT="${CLIPPER_PORT:-8000}"
[ "$MODE" = "--lan" ] && export CLIPPER_HOST=0.0.0.0

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg이 필요합니다. 설치 후 다시 실행하세요."
  echo "  macOS  : brew install ffmpeg"
  echo "  Ubuntu : sudo apt install ffmpeg"
  exit 1
fi

if [ "$MODE" = "--share" ]; then
  if ! command -v cloudflared >/dev/null; then
    echo "공개 주소를 만들려면 cloudflared가 필요합니다."
    echo "  macOS  : brew install cloudflared"
    echo "  Ubuntu : https://github.com/cloudflare/cloudflared/releases 에서 받기"
    exit 1
  fi
  if [ -z "${CLIPPER_PASSWORD:-}" ]; then
    # 발급되는 주소는 누구나 열 수 있다. 비밀번호 없이 열면 안 된다.
    echo "공개 주소는 링크만 알면 누구나 들어옵니다. 비밀번호를 정하고 다시 실행하세요."
    echo "  CLIPPER_PASSWORD=원하는비밀번호 ./run.sh --share"
    exit 1
  fi
fi

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

if [ "$MODE" = "--share" ]; then
  ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$CLIPPER_PORT" &
  APP_PID=$!
  trap 'kill "$APP_PID" 2>/dev/null || true' EXIT INT TERM
  echo
  echo "  아래 cloudflared가 찍어주는 https 주소로 어디서나 접속할 수 있습니다."
  echo "  이 터미널을 닫으면 주소도 함께 사라집니다."
  echo
  cloudflared tunnel --url "http://127.0.0.1:$CLIPPER_PORT"
else
  exec ./.venv/bin/uvicorn app.main:app --host "$CLIPPER_HOST" --port "$CLIPPER_PORT"
fi
