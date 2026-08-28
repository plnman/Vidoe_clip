#!/usr/bin/env bash
# 실행: ./run.sh          이 컴퓨터에서만 접속
#       ./run.sh --lan    같은 공유기의 다른 기기(노트북·폰)에서도 접속
# 처음 실행하면 가상환경을 만들고 의존성을 설치한다.
set -euo pipefail
cd "$(dirname "$0")"

export CLIPPER_HOST="${CLIPPER_HOST:-127.0.0.1}"
export CLIPPER_PORT="${CLIPPER_PORT:-8000}"
[ "${1:-}" = "--lan" ] && export CLIPPER_HOST=0.0.0.0

if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg이 필요합니다. 설치 후 다시 실행하세요."
  echo "  macOS  : brew install ffmpeg"
  echo "  Ubuntu : sudo apt install ffmpeg"
  exit 1
fi

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

exec ./.venv/bin/uvicorn app.main:app --host "$CLIPPER_HOST" --port "$CLIPPER_PORT"
