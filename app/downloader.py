"""yt-dlp 래퍼: 영상 정보 조회와 '필요한 구간만' 내려받기.

구간만 받는 게 이 앱의 핵심이다. 1시간짜리에서 2분만 쓰면 2분어치만 받는다.
편집 여유분(pad)을 앞뒤로 더 받아두기 때문에, 나중에 시작/끝을 조금 미세조정해도
다시 다운로드하지 않는다.
"""

from __future__ import annotations

import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, download_range_func

from . import config

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# yt-dlp가 오류 끝에 붙이는 "깃허브에 제보하세요" 안내. 사용자에게는 쓸모없다.
_NOISE_RE = re.compile(r"[;.]?\s*please report this issue.*", re.IGNORECASE | re.DOTALL)
_CAUSED_BY_RE = re.compile(r"\s*\(caused by .*?\)", re.DOTALL)
_PATH_ID_ROUTES = ("/shorts/", "/embed/", "/live/", "/v/")


class DownloadFailed(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass
class VideoInfo:
    video_id: str
    url: str
    title: str
    duration: float
    thumbnail: str
    uploader: str
    is_live: bool

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "title": self.title,
            "duration": round(self.duration, 3),
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
        }


def normalize_url(raw: str) -> str:
    """유튜브 URL만 받아 표준 watch URL로 바꾼다. 아니면 ValueError."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("링크를 입력하세요")
    if _VIDEO_ID_RE.match(raw):
        return f"https://www.youtube.com/watch?v={raw}"
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in config.ALLOWED_HOSTS:
        raise ValueError("유튜브 링크만 지원합니다")

    video_id = ""
    if host.endswith("youtu.be"):
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path == "/watch":
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        for route in _PATH_ID_ROUTES:
            if parsed.path.startswith(route):
                video_id = parsed.path[len(route) :].split("/")[0]
                break

    if not _VIDEO_ID_RE.match(video_id):
        raise ValueError("영상 ID를 찾지 못했습니다. 개별 영상 링크인지 확인하세요")
    return f"https://www.youtube.com/watch?v={video_id}"


def _format_selector(max_height: int, prefer: str) -> tuple[str, list[str]]:
    """(format, format_sort). prefer='compat'는 H.264/AAC, 'small'은 AV1/VP9 우선."""
    height = max(144, min(int(max_height or config.DEFAULT_HEIGHT), 4320))
    fmt = (
        f"bv*[height<={height}]+ba/b[height<={height}]"
        f"/bv*[height<={height}]/wv*+ba/w"
    )
    if prefer == "small":
        sort = ["vcodec:av01", "vcodec:vp9", "res", "br"]
    else:
        sort = ["vcodec:h264", "acodec:aac", "res", "br", "ext:mp4:m4a"]
    return fmt, sort


class _Silent:
    """yt-dlp가 콘솔에 직접 찍지 않게 한다. 오류는 예외로 받아 화면에 띄운다."""

    def debug(self, message): pass

    def info(self, message): pass

    def warning(self, message): pass

    def error(self, message): pass


# 유튜브는 자바스크립트 챌린지로 봇을 거른다. yt-dlp는 이걸 JS 런타임으로 푸는데,
# 기본 설정은 deno 하나만 본다. deno가 없는 PC가 대부분이므로 설치된 걸 찾아 넘긴다.
# (없으면 챌린지를 못 풀어 '봇으로 판단' 오류가 날 수 있다.)
# yt-dlp가 부르는 런타임 이름과 실제 실행 파일 이름이 늘 같지는 않다.
# quickjs의 실행 파일은 qjs다(-ng 갈래는 qjs-ng). 이름 그대로 찾으면 번들해도 못 찾는다.
JS_RUNTIMES: dict[str, tuple[str, ...]] = {
    "deno": ("deno",),
    "node": ("node",),
    "bun": ("bun",),
    "quickjs": ("qjs", "qjs-ng", "quickjs"),
}


def _find_runtime(executables: tuple[str, ...]) -> str | None:
    """함께 묶어 배포한 bin/을 먼저 보고, 없으면 PATH에서 찾는다."""
    bundled = config.bundled_bin_dir()
    if bundled:
        for name in executables:
            for candidate in (bundled / name, bundled / f"{name}.exe"):
                if candidate.exists():
                    return str(candidate)
    for name in executables:
        found = shutil.which(name)
        if found:
            return found
    return None


def available_js_runtimes() -> dict[str, dict]:
    found = {}
    for name, executables in JS_RUNTIMES.items():
        path = _find_runtime(executables)
        if path:
            found[name] = {"path": path}
    return found


def _base_opts() -> dict:
    runtimes = available_js_runtimes()
    bundled = config.bundled_bin_dir()
    opts: dict = {
        "logger": _Silent(),
        # 데스크톱 앱은 ffmpeg을 함께 묶어 배포한다. PATH에 없어도 찾게 알려준다.
        **({"ffmpeg_location": str(bundled)} if bundled else {}),
        # 찾은 게 없으면 yt-dlp 기본값(deno)에 맡긴다
        **({"js_runtimes": runtimes} if runtimes else {}),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "ignoreerrors": False,
    }
    if config.COOKIE_FILE:
        opts["cookiefile"] = config.COOKIE_FILE
    if config.COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (config.COOKIES_FROM_BROWSER,)
    if config.PROXY:
        opts["proxy"] = config.PROXY
    return opts


def _friendly(exc: Exception) -> str:
    """yt-dlp 원문 오류를 사람이 읽고 대처할 수 있는 한 줄로 바꾼다."""
    text = str(exc)
    lowered = text.lower()

    # "Sign in to confirm your age"와 겹치므로 봇 쪽은 'not a bot'으로 좁힌다
    if "not a bot" in lowered:
        return (
            "유튜브가 이 컴퓨터를 봇으로 판단해 막았습니다. 브라우저 쿠키를 넘기면 풀립니다 "
            "(CLIPPER_COOKIES). README의 '유튜브가 막을 때' 참고."
        )
    if "tunnel connection failed" in lowered or "proxyerror" in lowered:
        return "네트워크가 유튜브 접속을 막고 있습니다(프록시/방화벽). 다른 망에서 시도해 보세요."
    if "urlopen error" in lowered or "connection" in lowered and "refused" in lowered:
        return "유튜브에 연결하지 못했습니다. 인터넷 연결을 확인하세요."
    if "timed out" in lowered or "timeout" in lowered:
        return "유튜브 응답이 너무 느립니다. 잠시 후 다시 시도하세요."
    if "private video" in lowered:
        return "비공개 영상입니다"
    if "members-only" in lowered or "members only" in lowered:
        return "멤버십 전용 영상입니다"
    if "confirm your age" in lowered or "age-restricted" in lowered or "age restricted" in lowered:
        return "연령 제한 영상입니다. 로그인 쿠키(CLIPPER_COOKIES)가 필요합니다."
    # 'not available'이 겹치므로 화질 쪽을 먼저 본다
    if "requested format" in lowered:
        return "고른 화질로 받을 수 없습니다. 화질을 낮춰 보세요."
    if "video unavailable" in lowered or "not available" in lowered:
        return "볼 수 없는 영상입니다(삭제됐거나 지역 제한)"

    # 알려진 경우가 아니면 원문을 쓰되, 잡음은 걷어낸다
    text = _NOISE_RE.sub("", text)
    text = _CAUSED_BY_RE.sub("", text)
    text = re.sub(r"^ERROR:\s*", "", text.strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:240] or "다운로드에 실패했습니다"


def probe_url(url: str) -> VideoInfo:
    """다운로드 없이 제목·길이만 가져온다."""
    url = normalize_url(url)
    opts = _base_opts() | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise DownloadFailed(_friendly(exc)) from exc

    if info.get("is_live"):
        raise DownloadFailed("진행 중인 라이브는 지원하지 않습니다")
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise DownloadFailed("영상 길이를 알 수 없습니다")
    if duration > config.MAX_SOURCE_SECONDS:
        raise DownloadFailed(f"영상이 너무 깁니다(최대 {config.MAX_SOURCE_SECONDS // 3600}시간)")

    return VideoInfo(
        video_id=info.get("id") or "",
        url=url,
        title=info.get("title") or "제목 없음",
        duration=duration,
        thumbnail=info.get("thumbnail") or "",
        uploader=info.get("uploader") or info.get("channel") or "",
        is_live=False,
    )


def fetch_range(
    url: str,
    dest_dir: Path,
    name: str,
    *,
    start: float | None = None,
    end: float | None = None,
    max_height: int = config.DEFAULT_HEIGHT,
    prefer: str = "compat",
    exact: bool = True,
    on_progress=None,
    cancel: threading.Event | None = None,
) -> Path:
    """[start, end) 구간을 받아 파일 경로를 돌려준다. start/end가 없으면 전체.

    exact=True면 컷 지점에 키프레임을 강제해 파일이 정확히 start에서 시작한다.
    (구간 시각을 신뢰할 수 있게 만드는 대신 다운로드가 느려진다.)
    """
    url = normalize_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for stale in dest_dir.glob(f"{name}.*"):
        stale.unlink(missing_ok=True)

    fmt, sort = _format_selector(max_height, prefer)
    opts = _base_opts() | {
        "format": fmt,
        "format_sort": sort,
        "merge_output_format": "mp4",
        "outtmpl": {"default": str(dest_dir / f"{name}.%(ext)s")},
        "concurrent_fragment_downloads": 8,
        "overwrites": True,
    }

    if start is not None and end is not None:
        opts["download_ranges"] = download_range_func(None, [(float(start), float(end))])
        opts["force_keyframes_at_cuts"] = bool(exact)

    def hook(status: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled("취소되었습니다")
        if on_progress is None or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        done = status.get("downloaded_bytes") or 0
        if total:
            on_progress(min(1.0, done / total))

    opts["progress_hooks"] = [hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Cancelled:
        raise
    except DownloadError as exc:
        raise DownloadFailed(_friendly(exc)) from exc

    produced = sorted(
        (p for p in dest_dir.glob(f"{name}.*") if p.suffix.lower() != ".part"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not produced:
        raise DownloadFailed("받은 파일을 찾지 못했습니다")
    return produced[0]
