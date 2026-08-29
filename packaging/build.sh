#!/usr/bin/env bash
# 설치형 앱을 만든다 (macOS / Linux). 결과: dist/YoutubeClipper/
#
#   ./packaging/build.sh
#
# 크로스 빌드는 안 된다. 맥 앱은 맥에서, 리눅스용은 리눅스에서 만들어야 한다.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=./.venv/bin/python
[ -x "$PYTHON" ] || python3 -m venv .venv

echo "== 의존성 =="
"$PYTHON" -m pip install -q --upgrade pip
"$PYTHON" -m pip install -q -r requirements.txt
"$PYTHON" -m pip install -q pyinstaller pywebview

echo "== 함께 넣을 바이너리 =="
./packaging/fetch-binaries.sh

echo "== 빌드 =="
rm -rf build dist
"$PYTHON" -m PyInstaller --noconfirm --clean packaging/clipper.spec

# ffmpeg 등은 실행 파일 옆 bin/ 에 있어야 한다(config.bundled_bin_dir).
if [ -d packaging/bin ]; then
  echo "== bin/ 복사 =="
  mkdir -p dist/YoutubeClipper/bin
  cp -f packaging/bin/* dist/YoutubeClipper/bin/
  chmod +x dist/YoutubeClipper/bin/* || true
fi
cp -f LICENSE dist/YoutubeClipper/ 2>/dev/null || true

echo
echo "완성: dist/YoutubeClipper  ($(du -sh dist/YoutubeClipper | cut -f1))"

case "$(uname -s)" in
  Darwin)
    echo
    echo "설치 파일(dmg)을 만들려면:"
    echo "  hdiutil create -volname YoutubeClipper -srcfolder dist/YoutubeClipper \\"
    echo "                 -ov -format UDZO dist/YoutubeClipper.dmg"
    echo "서명하지 않으면 처음 열 때 우클릭 → 열기가 필요하다."
    ;;
  Linux)
    echo
    echo "배포하려면: tar -C dist -czf dist/YoutubeClipper.tar.gz YoutubeClipper"
    ;;
esac
