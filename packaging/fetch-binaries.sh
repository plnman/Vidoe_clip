#!/usr/bin/env bash
# 함께 배포할 바이너리를 packaging/bin 에 모은다 (macOS / Linux).
#
#   ffmpeg / ffprobe : 영상을 자르고 이어붙인다. 없으면 앱이 아무것도 못 한다.
#   qjs              : 유튜브의 자바스크립트 챌린지를 푼다. 없으면 '봇으로 판단' 오류.
#
# 라이선스 — libx264가 포함된 ffmpeg 빌드는 GPL이다. 함께 배포하면 이 앱도
# GPL로 배포해야 한다(LICENSE 참고). docs/DESKTOP.md 2.3에 정리해 두었다.
set -euo pipefail
cd "$(dirname "$0")"

BIN="$PWD/bin"
mkdir -p "$BIN"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

OS="$(uname -s)"
ARCH="$(uname -m)"

# --- ffmpeg -----------------------------------------------------------------
if [ -x "$BIN/ffmpeg" ] && [ -x "$BIN/ffprobe" ]; then
  echo "ffmpeg: 이미 있음 — 건너뜁니다"
elif [ "$OS" = "Darwin" ]; then
  # evermeet.cx는 ffmpeg과 ffprobe를 따로 준다
  for tool in ffmpeg ffprobe; do
    echo "$tool 받는 중 ..."
    curl -fsSL "https://evermeet.cx/ffmpeg/getrelease/$tool/zip" -o "$WORK/$tool.zip"
    unzip -oq "$WORK/$tool.zip" -d "$WORK"
    install -m 755 "$WORK/$tool" "$BIN/$tool"
  done
else
  case "$ARCH" in
    x86_64) FF_ARCH=amd64 ;;
    aarch64|arm64) FF_ARCH=arm64 ;;
    *) echo "지원하지 않는 아키텍처: $ARCH" >&2; exit 1 ;;
  esac
  echo "ffmpeg 받는 중 (johnvansickle static, $FF_ARCH) ..."
  curl -fsSL "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-$FF_ARCH-static.tar.xz" \
       -o "$WORK/ffmpeg.tar.xz"
  tar -xJf "$WORK/ffmpeg.tar.xz" -C "$WORK"
  for tool in ffmpeg ffprobe; do
    install -m 755 "$(find "$WORK" -type f -name "$tool" | head -1)" "$BIN/$tool"
  done
  cp -f "$(find "$WORK" -name 'GPLv3.txt' | head -1)" "$BIN/FFMPEG-LICENSE.txt" 2>/dev/null || true
fi

# --- quickjs ----------------------------------------------------------------
# node/deno/bun은 40~90MB인데 qjs는 1MB대다. 챌린지만 풀면 되므로 이걸로 충분하다.
if [ -x "$BIN/qjs" ]; then
  echo "qjs: 이미 있음 — 건너뜁니다"
else
  case "$OS-$ARCH" in
    Darwin-arm64)  QJS=qjs-darwin-arm64 ;;
    Darwin-x86_64) QJS=qjs-darwin-x86_64 ;;
    Linux-x86_64)  QJS=qjs-linux-x86_64 ;;
    Linux-aarch64) QJS=qjs-linux-aarch64 ;;
    *) echo "qjs: $OS-$ARCH 용 빌드가 없습니다 — 건너뜁니다" >&2; QJS="" ;;
  esac
  if [ -n "$QJS" ]; then
    echo "qjs 받는 중 ($QJS) ..."
    curl -fsSL "https://github.com/quickjs-ng/quickjs/releases/latest/download/$QJS" -o "$BIN/qjs"
    chmod +x "$BIN/qjs"
  fi
fi

echo
echo "packaging/bin 준비 완료:"
ls -lh "$BIN" | tail -n +2 | awk '{printf "  %-24s %8s\n", $9, $5}'
