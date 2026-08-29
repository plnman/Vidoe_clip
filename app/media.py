"""ffmpeg/ffprobe 래퍼.

편집 결과는 '입력별 정확 컷 + concat 필터' 한 번의 패스로 만든다.
소스 조각은 이미 받아둔 로컬 파일이라, 구간을 고쳐 다시 렌더해도 재다운로드가 없다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from . import config

def _tool(name: str) -> str:
    """함께 묶어 배포한 것을 먼저 쓰고, 없으면 PATH에서 찾는다."""
    bundled = config.bundled_bin_dir()
    if bundled:
        for candidate in (bundled / name, bundled / f"{name}.exe"):
            if candidate.exists():
                return str(candidate)
    return shutil.which(name) or name


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")

_PROGRESS_RE = re.compile(r"^(out_time_us|out_time_ms|progress)=(.*)$")


class MediaError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass
class Cut:
    """렌더에 넘길 한 조각: 파일 내부 시각 기준 [start, end) 구간(초)."""

    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_video: bool
    has_audio: bool


def ensure_tools() -> None:
    if not (shutil.which(FFMPEG) or Path(FFMPEG).exists()):
        raise MediaError("ffmpeg 을(를) 찾을 수 없습니다. 설치 후 다시 실행하세요.")


# ffmpeg은 UTF-8로 찍는데 파이썬은 OS 기본 인코딩으로 읽는다. 한글 윈도우(cp949)에서
# 한글이 섞인 경로나 메타데이터가 나오면 그 순간 UnicodeDecodeError로 죽는다.
# 오류 문구를 좀 흘리더라도 죽지는 않도록 항상 UTF-8로 읽고 못 읽는 바이트는 넘긴다.
_TEXT = {"encoding": "utf-8", "errors": "replace"}


def probe(path: Path) -> MediaInfo:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_TEXT)
    if proc.returncode != 0:
        raise MediaError(f"파일을 읽지 못했습니다: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for candidate in (data.get("format", {}).get("duration"), (video or {}).get("duration")):
        try:
            duration = max(duration, float(candidate))
        except (TypeError, ValueError):
            continue

    fps = 30.0
    if video and video.get("avg_frame_rate") not in ("0/0", "", None):
        num, _, den = str(video["avg_frame_rate"]).partition("/")
        try:
            fps = float(num) / float(den or 1) or 30.0
        except (ValueError, ZeroDivisionError):
            fps = 30.0

    return MediaInfo(
        duration=duration,
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
        fps=round(fps, 3),
        has_video=video is not None,
        has_audio=audio is not None,
    )


def _run_with_progress(cmd, total_seconds: float, on_progress, cancel: threading.Event | None) -> None:
    """ffmpeg을 돌리며 진행률(0~1)을 보고한다."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, **_TEXT
    )
    tail: list[str] = []

    def drain_stderr() -> None:
        for line in proc.stderr:
            tail.append(line.rstrip())
            del tail[:-40]

    watcher = threading.Thread(target=drain_stderr, daemon=True)
    watcher.start()
    cancelled = False

    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                cancelled = True
                proc.kill()
                break
            match = _PROGRESS_RE.match(line.strip())
            if not match or on_progress is None or total_seconds <= 0:
                continue
            key, value = match.groups()
            if key in ("out_time_us", "out_time_ms"):
                try:
                    micros = float(value)
                except ValueError:
                    continue
                seconds = micros / (1e6 if key == "out_time_us" else 1e3)
                on_progress(min(1.0, max(0.0, seconds / total_seconds)))
    finally:
        proc.wait()
        watcher.join(timeout=1)

    if cancelled:
        raise Cancelled("취소되었습니다")
    if proc.returncode != 0:
        raise MediaError("ffmpeg 실패: " + " / ".join(tail[-6:] or ["원인 불명"]))


def _norm_filters(info: MediaInfo, width: int, height: int, fps: float) -> str:
    """해상도·화면비·프레임레이트를 맞춰 concat이 안전하게 붙도록 한다."""
    filters = []
    if (info.width, info.height) != (width, height):
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    filters += ["setsar=1", f"fps={fps}"]
    return "," + ",".join(filters)


# 출력 포맷. presets는 품질 단계별 (인코더 설정, 화질값) 이다.
FORMATS: dict[str, dict] = {
    "mp4": {
        "label": "MP4 (H.264) — 어디서나 재생",
        "ext": ".mp4", "video": True, "audio": True, "vcodec": "libx264",
        "presets": {"fast": ("veryfast", 23), "balanced": ("medium", 20), "quality": ("slow", 18)},
    },
    "mp4_hevc": {
        "label": "MP4 (H.265) — 용량 절반, 최신 기기만",
        "ext": ".mp4", "video": True, "audio": True, "vcodec": "libx265",
        "presets": {"fast": ("veryfast", 28), "balanced": ("medium", 25), "quality": ("slow", 23)},
    },
    "webm": {
        "label": "WebM (VP9) — 용량 작음, 웹 업로드용",
        "ext": ".webm", "video": True, "audio": True, "vcodec": "libvpx-vp9",
        "presets": {"fast": ("5", 36), "balanced": ("3", 32), "quality": ("1", 28)},
    },
    "gif": {
        "label": "GIF — 짧은 구간용 (소리 없음)",
        "ext": ".gif", "video": True, "audio": False,
        "presets": {"fast": (10, 360), "balanced": (12, 480), "quality": (15, 640)},
    },
    "m4a": {"label": "M4A — 소리만 (고음질)", "ext": ".m4a", "video": False, "audio": True},
    "mp3": {"label": "MP3 — 소리만", "ext": ".mp3", "video": False, "audio": True},
}

DEFAULT_FORMAT = "mp4"
MAX_GIF_SECONDS = 60


def format_spec(fmt: str) -> dict:
    spec = FORMATS.get(fmt)
    if spec is None:
        raise MediaError(f"지원하지 않는 포맷입니다: {fmt}")
    return spec


def _quality_of(spec: dict, quality: str):
    presets = spec.get("presets") or {}
    return presets.get(quality) or presets.get("fast")


def _encode_args(fmt: str, quality: str, want_video: bool, want_audio: bool) -> list[str]:
    """포맷별 인코딩 옵션. 매핑은 호출하는 쪽에서 붙인다."""
    spec = format_spec(fmt)
    args: list[str] = []

    if want_video and fmt != "gif":
        setting, crf = _quality_of(spec, quality)
        if spec["vcodec"] == "libvpx-vp9":
            args += ["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0",
                     "-cpu-used", setting, "-row-mt", "1", "-pix_fmt", "yuv420p"]
        else:
            args += ["-c:v", spec["vcodec"], "-preset", setting, "-crf", str(crf),
                     "-pix_fmt", "yuv420p"]
            if spec["vcodec"] == "libx265":
                args += ["-tag:v", "hvc1"]  # 애플 기기에서 재생되게
    elif fmt == "gif":
        args += ["-loop", "0"]

    if want_audio:
        if fmt == "mp3":
            args += ["-c:a", "libmp3lame", "-q:a", "2"]
        elif fmt == "webm":
            args += ["-c:a", "libopus", "-b:a", "128k"]
        else:
            args += ["-c:a", "aac", "-b:a", "192k"]

    if spec["ext"] == ".mp4" or fmt == "m4a":
        args += ["-movflags", "+faststart"]
    return args


def render(
    cuts: list[Cut],
    out_path: Path,
    *,
    fmt: str = DEFAULT_FORMAT,
    quality: str = "fast",
    on_progress=None,
    cancel: threading.Event | None = None,
) -> Path:
    """조각들을 순서대로 잘라 하나로 이어붙인다."""
    spec = format_spec(fmt)
    cuts = [c for c in cuts if c.duration > 0.01]
    if not cuts:
        raise MediaError("이어붙일 구간이 없습니다")

    infos = [probe(c.path) for c in cuts]
    total = sum(c.duration for c in cuts)
    if fmt == "gif" and total > MAX_GIF_SECONDS:
        raise MediaError(f"GIF는 {MAX_GIF_SECONDS}초 이하만 만들 수 있습니다 (지금 {int(total)}초)")

    # 하나라도 영상이 없으면 영상 트랙을 만들 수 없다(무음과 달리 검은 화면은 의도가 아님).
    want_video = spec["video"] and all(info.has_video for info in infos)
    want_audio = spec["audio"]
    if not want_video and not want_audio:
        raise MediaError("이 포맷으로 만들 수 있는 트랙이 없습니다")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    for cut in cuts:
        cmd += ["-ss", f"{cut.start:.3f}", "-to", f"{cut.end:.3f}", "-i", str(cut.path)]

    # 오디오가 없는 조각은 무음으로 채워야 concat의 스트림 수가 맞는다.
    silent_for: dict[int, int] = {}
    if want_audio:
        for i, (cut, info) in enumerate(zip(cuts, infos)):
            if not info.has_audio:
                silent_for[i] = len(cuts) + len(silent_for)
                cmd += ["-f", "lavfi", "-t", f"{cut.duration:.3f}",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    width = max((i.width for i in infos if i.width), default=0)
    height = max((i.height for i in infos if i.height), default=0)
    fps = max((i.fps for i in infos if i.fps), default=30.0)

    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, info in enumerate(infos):
        if want_video:
            parts.append(f"[{i}:v]setpts=PTS-STARTPTS{_norm_filters(info, width, height, fps)}[v{i}]")
            concat_inputs.append(f"[v{i}]")
        if want_audio:
            src = f"[{silent_for[i]}:a]" if i in silent_for else f"[{i}:a]"
            parts.append(
                f"{src}asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
            concat_inputs.append(f"[a{i}]")

    n = len(cuts)
    concat = f"concat=n={n}:v={1 if want_video else 0}:a={1 if want_audio else 0}"
    labels = ("[v]" if want_video else "") + ("[a]" if want_audio else "")
    parts.append("".join(concat_inputs) + concat + labels)

    maps: list[str] = []
    if fmt == "gif":
        # 팔레트를 따로 뽑아야 색이 뭉개지지 않는다
        gif_fps, gif_width = _quality_of(spec, quality)
        parts.append(
            f"[v]fps={gif_fps},scale={gif_width}:-1:flags=lanczos,split[gv][gp];"
            f"[gp]palettegen=stats_mode=diff[pal];[gv][pal]paletteuse=dither=bayer:bayer_scale=3[out]"
        )
        maps += ["-map", "[out]"]
    else:
        if want_video:
            maps += ["-map", "[v]"]
        if want_audio:
            maps += ["-map", "[a]"]

    cmd += ["-filter_complex", ";".join(parts)]
    cmd += maps
    cmd += _encode_args(fmt, quality, want_video, want_audio)
    cmd.append(str(out_path))

    _run_with_progress(cmd, total, on_progress, cancel)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise MediaError("결과 파일이 만들어지지 않았습니다")
    return out_path


def make_thumbnail(src: Path, out_path: Path, at: float = 0.0) -> Path | None:
    """구간 카드에 쓸 썸네일 한 장."""
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{max(0.0, at):.3f}", "-i", str(src),
           "-frames:v", "1", "-vf", "scale=320:-2", str(out_path)]
    if subprocess.run(cmd, capture_output=True).returncode == 0 and out_path.exists():
        return out_path
    return None
