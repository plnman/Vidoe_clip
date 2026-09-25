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
import time
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

# out_time_ms 는 이름과 달리 값이 마이크로초다(ffmpeg의 오래된 quirk). 밀리초로 읽으면
# 진행률이 1000배가 되어 시작하자마자 100%가 된다. 그래서 out_time_us 만 쓴다.
_PROGRESS_RE = re.compile(r"^(out_time_us|progress)=(.*)$")


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
    title: str = ""

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


def _run_with_progress(
    cmd, total_seconds: float, on_progress, cancel: threading.Event | None,
    cwd: Path | None = None, on_phase=None,
) -> None:
    """ffmpeg을 돌리며 진행률(0~1)을 보고한다.

    마지막 프레임을 넘기면 진행률이 더 올라가지 않는데, 그 뒤로도 파일을 마무리하는
    시간이 꽤 걸린다(mp4는 moov를 앞으로 옮기느라 파일 전체를 다시 쓴다).
    그 구간을 알리지 않으면 화면이 99%에서 멈춘 것처럼 보인다.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        cwd=str(cwd) if cwd else None, **_TEXT,
    )
    tail: list[str] = []

    def drain_stderr() -> None:
        for line in proc.stderr:
            tail.append(line.rstrip())
            del tail[:-40]

    watcher = threading.Thread(target=drain_stderr, daemon=True)
    watcher.start()
    cancelled = False
    finalizing = False

    def mark_finalizing() -> None:
        nonlocal finalizing
        if not finalizing:
            finalizing = True
            if on_phase:
                on_phase("finalizing")

    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                cancelled = True
                proc.kill()
                break
            match = _PROGRESS_RE.match(line.strip())
            if not match or total_seconds <= 0:
                continue
            if on_progress is None and match.group(1) != "progress":
                continue  # 진행률을 안 받더라도 끝났다는 신호는 봐야 한다
            key, value = match.groups()
            if key == "progress":
                # 인코딩이 끝났다는 신호. 이 뒤로도 파일을 마무리하는 시간이 남아 있다.
                if value.strip() == "end":
                    mark_finalizing()
                continue
            try:
                micros = float(value)  # out_time_us
            except ValueError:
                continue  # 시작 직후에는 N/A 가 온다
            on_progress(min(1.0, max(0.0, micros / 1e6 / total_seconds)))
    finally:
        proc.wait()
        watcher.join(timeout=1)

    if cancelled:
        raise Cancelled("취소되었습니다")
    if proc.returncode != 0:
        raise MediaError("ffmpeg 실패: " + " / ".join(tail[-6:] or ["원인 불명"]))


# 구간 제목을 화면에 얹으려면 글꼴 파일이 필요하다. 한글이 나와야 하므로
# 한글을 담은 것을 먼저 찾는다. 함께 묶어 배포할 때는 bin/font.ttf를 넣으면 그게 1순위다.
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\NanumGothic.ttf",
    r"C:\Windows\Fonts\gulim.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/AppleGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def find_font() -> Path | None:
    bundled = config.bundled_bin_dir()
    if bundled:
        for name in ("font.ttf", "font.ttc", "font.otf"):
            candidate = bundled / name
            if candidate.exists():
                return candidate
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


TITLE_FONT_NAME = "font.ttf"


def _title_filters(cuts: list[Cut], workdir: Path) -> list[str]:
    """구간마다 제목을 화면 위쪽에 얹는 drawtext 필터.

    파일 이름을 경로 없이 쓰는 이유 — 필터그래프 안의 경로는 탈출 규칙이 지독하다.
    윈도우의 `C:\\...`는 콜론이 옵션 구분자와 겹쳐서 어떻게 탈출해도 잘 깨진다.
    그래서 ffmpeg을 이 폴더에서 실행하고(cwd) 여기서는 `title0.txt` 같은 이름만 쓴다.
    글꼴도 같은 이유로 이 폴더에 복사해 둔다.

    제목 글자 자체도 파일로 넘긴다. 콜론·쉼표·따옴표가 든 문장을 그대로 넣으면
    같은 문제가 생기는데, 제목은 사용자가 붙여넣은 아무 문장이기 때문이다.
    """
    filters = []
    offset = 0.0
    for index, cut in enumerate(cuts):
        title = (cut.title or "").strip()
        if title:
            (workdir / f"title{index}.txt").write_text(title, encoding="utf-8")
            filters.append(
                "drawtext="
                f"fontfile={TITLE_FONT_NAME}:"
                f"textfile=title{index}.txt:"
                # 제목은 사용자가 쓴 문장 그대로여야 한다. 끄지 않으면 drawtext가
                # `%{...}`를 시각·파일명 같은 것으로 바꾸려 들고 `\`도 escape로 삼는다.
                "expansion=none:"
                "fontcolor=white@0.95:"
                # 화면 높이에 맞춰 커지고 작아진다. 어떤 해상도에서도 비슷하게 보이도록.
                "fontsize=h/24:"
                "box=1:boxcolor=black@0.4:boxborderw=14:"
                "x=(w-text_w)/2:y=h*0.04:"
                # 그 구간이 나오는 동안만. 완성본 기준 시각이다.
                f"enable=between(t\\,{offset:.3f}\\,{offset + cut.duration:.3f})"
            )
        offset += cut.duration
    return filters


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

# GPU의 전용 인코딩 회로. 같은 영상을 CPU보다 몇 배 빨리 만든다. 같은 용량 대비
# 화질은 조금 떨어지지만 클립 저장 용도에는 차이를 느끼기 어렵다.
# 순서가 우선순위다. 먼저 되는 것을 쓴다.
_HW_CANDIDATES = {
    "libx264": [("h264_nvenc", "NVIDIA"), ("h264_qsv", "Intel"),
                ("h264_amf", "AMD"), ("h264_videotoolbox", "Apple")],
    "libx265": [("hevc_nvenc", "NVIDIA"), ("hevc_qsv", "Intel"),
                ("hevc_amf", "AMD"), ("hevc_videotoolbox", "Apple")],
}

_hw_cache: dict[str, tuple[str, str] | None] = {}
# 어느 인코더를 시도했고 왜 안 됐는지. "CPU 사용"만 보여주면 손쓸 방법이 없다.
_hw_attempts: dict[str, list[dict]] = {}
_encoder_list: set[str] | None = None


def _compiled_encoders() -> set[str]:
    """이 ffmpeg에 들어 있는 인코더 이름. 없는 것은 돌려볼 필요도 없다."""
    global _encoder_list
    if _encoder_list is None:
        try:
            done = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                                  capture_output=True, timeout=20, **_TEXT)
            _encoder_list = set(re.findall(r"^\s*[VAS][.A-Z]{5}\s+(\S+)",
                                           done.stdout or "", re.MULTILINE))
        except (OSError, subprocess.SubprocessError):
            _encoder_list = set()
    return _encoder_list


def _encoder_works(name: str) -> tuple[bool, str]:
    """실제로 짧은 영상을 인코딩해 본다. (되는지, 안 되면 이유)

    `ffmpeg -encoders` 목록에 있다고 되는 게 아니다. 윈도우용 ffmpeg 빌드는
    NVIDIA 카드가 없어도 h264_nvenc를 목록에 넣어두기 때문에, 돌려봐야 안다.

    해상도를 640x360으로 잡는 이유 — 하드웨어 인코더는 너무 작은 화면을 거부하는
    경우가 있다. 실제 쓰임에 가까운 크기로 시험해야 헛되이 걸러지지 않는다.
    """
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi",
           "-i", "color=c=black:s=640x360:r=30:d=1", "-c:v", name, "-f", "null", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=40, **_TEXT)
    except subprocess.TimeoutExpired:
        return False, "시험 인코딩이 40초 안에 끝나지 않았습니다"
    except OSError as exc:
        return False, str(exc)
    if done.returncode == 0:
        return True, ""
    reason = " / ".join((done.stderr or "").strip().splitlines()[-3:])
    return False, reason or f"ffmpeg 종료 코드 {done.returncode}"


# 속도를 견줄 때 쓰는 시험 영상. 실제 쓰임에 가까워야 의미가 있다.
# 너무 짧으면 GPU를 깨우는 시간이 결과를 지배해 억울하게 느려 보인다.
_BENCH_SOURCE = "testsrc2=size=1280x720:rate=30:d=5"
_BENCH_SECONDS = 5.0


def _encode_seconds(name: str, extra: list[str]) -> float | None:
    """그 인코더로 시험 영상을 만드는 데 걸린 시간. 실패하면 None."""
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", _BENCH_SOURCE,
           "-c:v", name, *extra, "-pix_fmt", "yuv420p", "-f", "null", "-"]
    began = time.perf_counter()
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=120, **_TEXT)
    except (OSError, subprocess.SubprocessError):
        return None
    return time.perf_counter() - began if done.returncode == 0 else None


# 재는 데 몇 초가 든다. 앱을 켤 때마다 다시 재면 그만큼 첫 화면이 늦는다.
# ffmpeg이 바뀌지 않는 한 결과도 바뀌지 않으므로 파일에 적어두고 다시 쓴다.
_CACHE_VERSION = 1


def _encoder_cache_file() -> Path:
    return config.user_data_dir() / "encoder.json"


def _fingerprint() -> str:
    """이 판단이 유효한 조건. 달라지면 다시 잰다."""
    path = shutil.which(FFMPEG) or FFMPEG
    try:
        stat = Path(path).stat()
        mark = f"{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        mark = "?"
    return f"{_CACHE_VERSION}|{path}|{mark}|{config.HARDWARE}"


def _load_cached_choice(vcodec: str) -> bool:
    """전에 재둔 결과가 아직 유효하면 가져온다."""
    try:
        saved = json.loads(_encoder_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if saved.get("fingerprint") != _fingerprint() or vcodec not in saved.get("codecs", {}):
        return False
    entry = saved["codecs"][vcodec]
    found = entry.get("found")
    _hw_cache[vcodec] = tuple(found) if found else None
    _hw_attempts[vcodec] = entry.get("attempts", [])
    return True


def _save_cached_choice(vcodec: str) -> None:
    path = _encoder_cache_file()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("fingerprint") != _fingerprint():
            saved = {}
    except (OSError, ValueError):
        saved = {}
    codecs = saved.get("codecs", {}) if saved else {}
    found = _hw_cache.get(vcodec)
    codecs[vcodec] = {"found": list(found) if found else None,
                      "attempts": _hw_attempts.get(vcodec, [])}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fingerprint": _fingerprint(), "codecs": codecs}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass  # 못 적어도 다음에 다시 재면 그만이다


# 이만큼은 빨라야 GPU로 바꿀 값어치가 있다. 재는 값에 흔들림이 있어서,
# 차이가 미미하면 켤 때마다 고르는 쪽이 달라진다.
_HW_MARGIN = 1.1


def _hardware_beats_cpu(name: str) -> tuple[bool, str]:
    """GPU가 정말 CPU보다 빠른가. (빠른가, 사람이 읽을 설명)

    되는 것과 빠른 것은 다르다. 보급형 GPU의 인코더는 요즘 CPU의 x264보다 느린 일이
    흔하다 — 실제로 이 프로젝트의 사용 기기(Radeon RX 550)에서 1080p 60초를 만드는 데
    GPU 19.2초, CPU 15.5초였다. 파일도 GPU 쪽이 1.4배 컸다.
    '쓸 수 있으니 쓴다'로 두면 느려지는 쪽을 고르게 된다. 그래서 재보고 고른다.
    """
    gpu = _encode_seconds(name, _hw_quality_args(name, "fast"))
    if gpu is None:
        return False, "시험 인코딩에 실패했습니다"
    cpu = _encode_seconds("libx264", ["-preset", "veryfast", "-crf", "23"])
    if cpu is None:
        return True, ""  # CPU 쪽을 못 쟀으면 판단 근거가 없다. 되는 것을 쓴다.

    speeds = f"GPU {_BENCH_SECONDS / gpu:.1f}배속 vs CPU {_BENCH_SECONDS / cpu:.1f}배속"
    if gpu * _HW_MARGIN < cpu:
        return True, speeds
    return False, f"CPU보다 느려서 쓰지 않습니다 ({speeds})"


def detect_hardware_encoder(vcodec: str = "libx264") -> tuple[str, str] | None:
    """쓸 수 있는 하드웨어 인코더 (이름, 제조사). 없으면 None. 한 번만 조사한다."""
    if config.HARDWARE == "off":
        return None
    if vcodec in _hw_cache:
        return _hw_cache[vcodec]
    if _load_cached_choice(vcodec):
        return _hw_cache[vcodec]
    compiled = _compiled_encoders()
    found = None
    attempts: list[dict] = []
    for name, vendor in _HW_CANDIDATES.get(vcodec, []):
        if compiled and name not in compiled:
            attempts.append({"encoder": name, "vendor": vendor,
                             "ok": False, "reason": "이 ffmpeg 빌드에 없습니다"})
            continue
        ok, reason = _encoder_works(name)
        if ok and config.HARDWARE != "force":
            # 되는 것과 빠른 것은 다르다. 느리면 안 쓰느니만 못하다.
            ok, reason = _hardware_beats_cpu(name)
        attempts.append({"encoder": name, "vendor": vendor, "ok": ok, "reason": reason})
        if ok:
            found = (name, vendor)
            break
    _hw_cache[vcodec] = found
    _hw_attempts[vcodec] = attempts
    _save_cached_choice(vcodec)
    for attempt in attempts:
        if attempt["ok"]:
            note = f" ({attempt['reason']})" if attempt["reason"] else ""
            print(f"[encoder] {attempt['encoder']}: 사용{note}", flush=True)
        else:
            print(f"[encoder] {attempt['encoder']}: 안 씀 — {attempt['reason']}", flush=True)
    return found


def hardware_attempts(vcodec: str = "libx264") -> list[dict]:
    """조사 결과. 아직 조사 전이면 조사부터 한다."""
    if vcodec not in _hw_attempts:
        detect_hardware_encoder(vcodec)
    return _hw_attempts.get(vcodec, [])


def _hw_quality_args(name: str, quality: str) -> list[str]:
    """하드웨어 인코더는 -preset/-crf를 안 쓴다. 종류마다 다른 이름을 쓴다."""
    level = {"fast": 0, "balanced": 1, "quality": 2}.get(quality, 0)
    if "nvenc" in name:
        return ["-preset", ("p2", "p4", "p6")[level], "-rc", "vbr",
                "-cq", ("28", "24", "21")[level], "-b:v", "0"]
    if "qsv" in name:
        return ["-preset", ("veryfast", "medium", "slow")[level],
                "-global_quality", ("28", "24", "21")[level]]
    if "amf" in name:
        qp = ("28", "24", "21")[level]
        return ["-quality", ("speed", "balanced", "quality")[level],
                "-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
    if "videotoolbox" in name:
        return ["-q:v", ("40", "55", "68")[level]]
    return []


def format_spec(fmt: str) -> dict:
    spec = FORMATS.get(fmt)
    if spec is None:
        raise MediaError(f"지원하지 않는 포맷입니다: {fmt}")
    return spec


def _quality_of(spec: dict, quality: str):
    presets = spec.get("presets") or {}
    return presets.get(quality) or presets.get("fast")


def _encode_args(
    fmt: str, quality: str, want_video: bool, want_audio: bool, hw: str | None = None
) -> list[str]:
    """포맷별 인코딩 옵션. 매핑은 호출하는 쪽에서 붙인다."""
    spec = format_spec(fmt)
    args: list[str] = []

    if want_video and fmt != "gif" and hw:
        args += ["-c:v", hw, *_hw_quality_args(hw, quality), "-pix_fmt", "yuv420p"]
        if hw.startswith("hevc") and spec["ext"] == ".mp4":
            args += ["-tag:v", "hvc1"]  # 애플 기기에서 재생되게
    elif want_video and fmt != "gif":
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
    titles: bool = False,
    on_progress=None,
    on_phase=None,
    warn=None,
    cancel: threading.Event | None = None,
) -> Path:
    """조각들을 순서대로 잘라 하나로 이어붙인다.

    titles=True면 구간 제목을 화면 위쪽에 자막처럼 얹는다. 글꼴을 못 찾으면
    제목만 빼고 나머지는 그대로 만든다 — 장식 때문에 완성본을 못 받으면 곤란하다.
    """
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

    # 제목은 이어붙인 뒤에 얹는다. 완성본 기준 시각이라야 구간마다 제때 나온다.
    video_label = "[v]"
    workdir: Path | None = None
    if titles and want_video and any((c.title or "").strip() for c in cuts):
        font = find_font()
        if font is None:
            if warn:
                warn("글꼴을 찾지 못해 구간 제목은 넣지 못했습니다")
        else:
            workdir = out_path.parent / f".titles-{out_path.stem}"
            shutil.rmtree(workdir, ignore_errors=True)
            workdir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(font, workdir / TITLE_FONT_NAME)
            drawn = _title_filters(cuts, workdir)
            if drawn:
                parts.append("[v]" + ",".join(drawn) + "[vt]")
                video_label = "[vt]"

    maps: list[str] = []
    if fmt == "gif":
        # 팔레트를 따로 뽑아야 색이 뭉개지지 않는다
        gif_fps, gif_width = _quality_of(spec, quality)
        parts.append(
            f"{video_label}fps={gif_fps},scale={gif_width}:-1:flags=lanczos,split[gv][gp];"
            f"[gp]palettegen=stats_mode=diff[pal];[gv][pal]paletteuse=dither=bayer:bayer_scale=3[out]"
        )
        maps += ["-map", "[out]"]
    else:
        if want_video:
            maps += ["-map", video_label]
        if want_audio:
            maps += ["-map", "[a]"]

    cmd += ["-filter_complex", ";".join(parts)]
    cmd += maps
    base_cmd = list(cmd)

    def full(use_hw: str | None) -> list[str]:
        return [*base_cmd,
                *_encode_args(fmt, quality, want_video, want_audio, hw=use_hw),
                str(out_path)]

    hardware = None
    if want_video and fmt != "gif":
        found = detect_hardware_encoder(spec["vcodec"])
        hardware = found[0] if found else None
        # 느리다는 이야기가 나왔을 때 무엇으로 만들었는지 바로 알 수 있어야 한다
        print(f"[render] {fmt} {quality} · 인코더 "
              f"{hardware or spec.get('vcodec', 'libx264') + ' (CPU)'} · "
              f"{total:.0f}초 · 구간 {len(cuts)}개", flush=True)

    try:
        # 제목을 넣을 때는 그 폴더에서 실행한다. 필터그래프가 상대 파일명을 쓰기 때문이다.
        try:
            _run_with_progress(full(hardware), total, on_progress, cancel,
                               cwd=workdir, on_phase=on_phase)
        except MediaError:
            # 조사에서는 됐는데 실제 인코딩에서 실패할 수 있다(드라이버, 해상도 제한 등).
            # 완성본을 못 받는 것보다 느리게라도 받는 편이 낫다.
            if hardware is None:
                raise
            _hw_cache[spec["vcodec"]] = None
            if warn:
                warn("하드웨어 인코더가 실패해 CPU로 다시 만듭니다")
            if on_progress:
                on_progress(0.0)
            _run_with_progress(full(None), total, on_progress, cancel,
                               cwd=workdir, on_phase=on_phase)
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
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
