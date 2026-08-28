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

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

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


def probe(path: Path) -> MediaInfo:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
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
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
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


def render(
    cuts: list[Cut],
    out_path: Path,
    *,
    audio_only: bool = False,
    preset: str = "veryfast",
    crf: int = 20,
    on_progress=None,
    cancel: threading.Event | None = None,
) -> Path:
    """조각들을 순서대로 잘라 하나로 이어붙인다."""
    cuts = [c for c in cuts if c.duration > 0.01]
    if not cuts:
        raise MediaError("이어붙일 구간이 없습니다")

    infos = [probe(c.path) for c in cuts]
    total = sum(c.duration for c in cuts)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    for cut in cuts:
        cmd += ["-ss", f"{cut.start:.3f}", "-to", f"{cut.end:.3f}", "-i", str(cut.path)]

    # 오디오가 없는 조각은 무음으로 채워야 concat의 스트림 수가 맞는다.
    silent_for: dict[int, int] = {}
    for i, (cut, info) in enumerate(zip(cuts, infos)):
        if not info.has_audio:
            silent_for[i] = len(cuts) + len(silent_for)
            cmd += ["-f", "lavfi", "-t", f"{cut.duration:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    # 하나라도 영상이 없으면 영상 트랙을 만들 수 없다(무음과 달리 검은 화면은 의도가 아님).
    want_video = not audio_only and all(info.has_video for info in infos)

    width = max((i.width for i in infos if i.width), default=0)
    height = max((i.height for i in infos if i.height), default=0)
    fps = max((i.fps for i in infos if i.fps), default=30.0)

    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, info in enumerate(infos):
        if want_video:
            parts.append(f"[{i}:v]setpts=PTS-STARTPTS{_norm_filters(info, width, height, fps)}[v{i}]")
            concat_inputs.append(f"[v{i}]")
        src = f"[{silent_for[i]}:a]" if i in silent_for else f"[{i}:a]"
        parts.append(f"{src}asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        concat_inputs.append(f"[a{i}]")

    n = len(cuts)
    parts.append(
        "".join(concat_inputs)
        + f"concat=n={n}:v={1 if want_video else 0}:a=1"
        + ("[v][a]" if want_video else "[a]")
    )
    cmd += ["-filter_complex", ";".join(parts)]

    if want_video:
        cmd += ["-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    elif out_path.suffix.lower() == ".mp3":
        cmd += ["-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2"]
    else:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]

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
