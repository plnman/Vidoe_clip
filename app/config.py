"""환경변수로 조정하는 설정값."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# 데스크톱 앱으로 묶였는지. PyInstaller가 실행 파일로 만들면 True.
FROZEN = getattr(sys, "frozen", False)


def user_data_dir() -> pathlib.Path:
    """OS별 사용자 데이터 폴더. 데스크톱 앱일 때 작업 파일을 여기에 둔다."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = pathlib.Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(base) / "YoutubeClipper"


def bundled_bin_dir() -> pathlib.Path | None:
    """함께 묶어 배포한 ffmpeg 등이 있는 폴더. 개발 중에는 없다."""
    if not FROZEN:
        return None
    # PyInstaller onedir: 실행 파일 옆의 bin/
    candidate = pathlib.Path(sys.executable).parent / "bin"
    return candidate if candidate.is_dir() else None


def use_bundled_bin() -> None:
    """묶어 온 bin/을 PATH 맨 앞에 둔다.

    옵션으로 알려주는 것만으로는 부족하다. yt-dlp는 '이 포맷을 구간만 받을 수 있나'를
    판단할 때 downloader 없이 FFmpegFD.available()을 부르는데, 그 자리에서는 우리가 넘긴
    ffmpeg_location이 보이지 않고 PATH만 본다. 그래서 ffmpeg이 옆에 있는데도
    "ffmpeg is not installed"로 거절당한다 — 이 앱의 기본 동작인 구간 다운로드가 통째로 막힌다.

    PATH에 넣어두면 그 판단도, qjs 같은 다른 도구 탐색도 한 번에 해결된다.
    """
    bundled = bundled_bin_dir()
    if bundled is None:
        return
    current = os.environ.get("PATH", "")
    if str(bundled) not in current.split(os.pathsep):
        os.environ["PATH"] = str(bundled) + os.pathsep + current


# 데스크톱 앱은 임시 폴더가 청소돼 결과물이 사라지면 곤란하므로 사용자 폴더를 쓴다
_DEFAULT_WORK_DIR = (
    user_data_dir() / "work" if FROZEN else pathlib.Path(tempfile.gettempdir()) / "yt-clipper"
)
WORK_DIR = pathlib.Path(os.environ.get("CLIPPER_WORK_DIR") or _DEFAULT_WORK_DIR).resolve()

# 실행 스크립트가 알려주는 바인딩 주소. 시작 안내문에만 쓴다.
HOST = os.environ.get("CLIPPER_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _int("CLIPPER_PORT", 8000)

# 접속 비밀번호. 비워두면 인증 없음(로컬 전용 가정).
PASSWORD = os.environ.get("CLIPPER_PASSWORD", "").strip()

# yt-dlp에 넘길 쿠키. 클라우드 IP에서 유튜브가 봇으로 막을 때 필요하다.
COOKIE_FILE = os.environ.get("CLIPPER_COOKIES", "").strip()
COOKIES_FROM_BROWSER = os.environ.get("CLIPPER_COOKIES_FROM_BROWSER", "").strip()
PROXY = os.environ.get("CLIPPER_PROXY", "").strip()

# 내 컴퓨터의 영상 파일을 쓸 때. 같은 PC의 파일은 경로만 받아 그 자리에서 쓰고(복사 없음),
# 다른 기기에서 접속했을 때만 올려받는다. 그래서 상한은 업로드에만 걸린다.
MAX_UPLOAD_BYTES = _int("CLIPPER_MAX_UPLOAD_MB", 8 * 1024) * 1024 * 1024
VIDEO_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg",
    ".ts", ".m2ts", ".3gp", ".ogv",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".opus", ".ogg", ".wma",
}

# 안전장치
MAX_SEGMENTS = _int("CLIPPER_MAX_SEGMENTS", 60)
MAX_TOTAL_SECONDS = _int("CLIPPER_MAX_TOTAL_SECONDS", 3 * 3600)
MAX_SOURCE_SECONDS = _int("CLIPPER_MAX_SOURCE_SECONDS", 6 * 3600)
PROJECT_TTL_SECONDS = _int("CLIPPER_PROJECT_TTL", 12 * 3600)
MAX_WORKERS = _int("CLIPPER_MAX_WORKERS", 2)

# 구간 앞뒤로 더 받아두는 여유분(초). 이 범위 안에서는 재다운로드 없이 미세조정된다.
# 크게 잡을수록 나중에 고치기 편하지만 받는 양이 늘고, 구간 다운로드는 재인코딩이라
# 그만큼 느려진다(DESIGN.md D5). 몇 초 다듬는 데는 2초면 충분하다.
DEFAULT_PAD = _int("CLIPPER_DEFAULT_PAD", 2)
MAX_PAD = _int("CLIPPER_MAX_PAD", 120)

DEFAULT_HEIGHT = _int("CLIPPER_DEFAULT_HEIGHT", 1080)

# 받을 양이 영상의 이 비율을 넘으면 통째로 받는 편이 빠르다.
#
# 두 경로의 속도가 구조적으로 다르기 때문이다. 전체 받기는 yt-dlp의 조각 다운로더가
# 여러 연결로 동시에 받는다. 구간 받기는 ffmpeg 한 프로세스가 단일 연결로 받는데,
# 유튜브는 단일 연결을 심하게 조인다. 실측(2026-08-29, 31분 영상):
#
#     전체 31:44 · 249MB  →  17.9초   (14 MB/s)
#     구간 30초          →  20.0초   (0.17 MB/s)
#
# 영상 전체를 받는 것이 30초 구간 하나보다 빨랐다. 이 비율이면 손익분기가 1%대라
# 사실상 거의 언제나 전체 받기가 이긴다. 그래도 아주 긴 영상에서 몇 초만 쓰는 경우는
# 있으므로 여유를 두고 잡는다.
WHOLE_FASTER_ABOVE = float(os.environ.get("CLIPPER_WHOLE_FASTER_ABOVE", "") or 0.08)

# 그래서 기본이 '전체 받기'다.
#
# 사용자가 얻는 것으로 따지면 두 방식의 차이는 이것뿐이다.
#
#            받는 시간        디스크        편집 자유도     결과물
#   전체     훨씬 짧다        영상 전체     영상 어디든     같다
#   구간     훨씬 길다        구간만        여유분 안에서   같다
#
# 손해는 디스크뿐이고 그건 작업 폴더에서 지우면 된다. 시간은 되돌릴 수 없다.
# 아주 긴 영상에서 몇 초만 쓰는 경우를 위해 끌 수는 있게 남겨둔다.
DEFAULT_WHOLE = (os.environ.get("CLIPPER_DEFAULT_WHOLE", "") or "1").strip().lower() not in (
    "0", "false", "no",
)

RENDER_PRESETS = {
    "fast": {"preset": "veryfast", "crf": 23, "label": "빠름"},
    "balanced": {"preset": "medium", "crf": 20, "label": "균형"},
    "quality": {"preset": "slow", "crf": 18, "label": "고품질"},
}

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
