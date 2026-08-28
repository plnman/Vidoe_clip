"""환경변수로 조정하는 설정값."""

from __future__ import annotations

import os
import pathlib
import tempfile


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


_DEFAULT_WORK_DIR = pathlib.Path(tempfile.gettempdir()) / "yt-clipper"
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
