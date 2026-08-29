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
DEFAULT_PAD = _int("CLIPPER_DEFAULT_PAD", 10)
MAX_PAD = _int("CLIPPER_MAX_PAD", 120)

DEFAULT_HEIGHT = _int("CLIPPER_DEFAULT_HEIGHT", 1080)

RENDER_PRESETS = {
    "fast": {"preset": "veryfast", "crf": 23, "label": "빠름"},
    "balanced": {"preset": "medium", "crf": 20, "label": "균형"},
    "quality": {"preset": "slow", "crf": 18, "label": "고품질"},
}

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
