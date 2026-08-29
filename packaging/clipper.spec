# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 설정. onedir로 만든다.

onefile이 아닌 이유(docs/DESKTOP.md 2.2) — 실행할 때마다 임시 폴더에 압축을 풀어
시작이 느리고, ffmpeg 같은 큰 바이너리를 함께 넣으면 더 심해진다. 백신 오탐도 잦다.
onedir로 만들고 설치 프로그램으로 감싸면 사용자에게는 어차피 파일 하나다.

ffmpeg 등 함께 배포할 바이너리는 여기서 넣지 않는다. datas로 넣으면 _internal/ 안으로
들어가는데, config.bundled_bin_dir()이 보는 자리는 실행 파일 옆의 bin/ 이다.
그래서 빌드 스크립트가 COLLECT 뒤에 복사한다.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

# yt-dlp는 추출기를 동적으로 import 한다. 정적 분석으로는 안 잡혀서 통째로 넣는다.
ytdlp_datas, ytdlp_binaries, ytdlp_hidden = collect_all("yt_dlp")

hidden = ytdlp_hidden + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # 업로드(멀티파트)와 파일 응답에 쓰인다
    "multipart",
    "python_multipart",
]
hidden += collect_submodules("webview.platforms")
# 진입점이 app.desktop만 import 하므로, 나머지 모듈은 이름으로 끌어와야 한다
hidden += collect_submodules("app")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=ytdlp_binaries,
    datas=[(str(ROOT / "app" / "static"), "app/static")] + ytdlp_datas,
    hiddenimports=hidden,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YoutubeClipper",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico") if (ROOT / "packaging" / "icon.ico").exists() else None,
)

coll = COLLECT(exe, a.binaries, a.datas, name="YoutubeClipper")
