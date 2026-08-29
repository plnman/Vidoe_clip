"""yt-dlp 연동을 실제 yt-dlp로 검증한다.

유튜브에는 붙지 않는다. 대신 로컬 HTTP 서버가 내려주는 영상에 대해
'구간만 받기'를 실제로 돌려서, 받은 파일이 요청한 시각에서 정확히 시작하는지 본다.
이 전제가 깨지면 여유분(pad) 계산이 통째로 어긋나므로 가장 중요한 확인이다.

서버는 HTTP Range를 지원한다. 영상 서버는 모두 지원하고, 지원하지 않으면
ffmpeg이 파일을 탐색하지 못해 구간 받기 자체가 실패한다.
"""

import http.server
import os
import pathlib
import socket
import socketserver
import subprocess
import threading

import pytest

from app import downloader

# 픽스처가 갈아끼우기 전의 진짜 함수 (링크 검사 자체를 시험하는 데 쓴다)
REAL_NORMALIZE_URL = downloader.normalize_url

# 10초씩 색이 바뀌는 60초 영상. 색을 보면 소스의 몇 초 지점인지 알 수 있다.
COLORS = ["red", "green", "blue", "yellow", "magenta", "cyan"]
RGB = {
    "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "magenta": (255, 0, 255), "cyan": (0, 255, 255),
}
BLOCK = 10
TOTAL = BLOCK * len(COLORS)


def _build_source(path):
    """색 블록 6개 + 소리를 담은 60초 mp4. 실제 스트리밍 파일처럼 moov를 앞에 둔다."""
    inputs, filters = [], []
    for i, color in enumerate(COLORS):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=10:duration={BLOCK}"]
        filters.append(f"[{i}:v]")
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={TOTAL}"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", "".join(filters) + f"concat=n={len(COLORS)}:v=1:a=0[v]",
           "-map", "[v]", "-map", f"{len(COLORS)}:a",
           "-c:v", "libx264", "-preset", "ultrafast", "-g", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _sample_color(path, at):
    """결과 파일 at초 지점의 평균색 (r, g, b)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    assert len(out) >= 3, "프레임을 읽지 못했습니다"
    return tuple(out[:3])


def _nearest_color(rgb):
    return min(RGB, key=lambda name: sum((a - b) ** 2 for a, b in zip(RGB[name], rgb)))


def _range_handler(root: pathlib.Path):
    class Handler(http.server.BaseHTTPRequestHandler):
        """Range를 지원하는 최소 서버."""

        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self._serve(head_only=True)

        def do_GET(self):
            self._serve()

        def _serve(self, head_only=False):
            path = root / os.path.basename(self.path.split("?")[0])
            if not path.exists():
                self.send_error(404)
                return
            size = path.stat().st_size
            start, end, partial = 0, size - 1, False
            header = self.headers.get("Range")
            if header and header.startswith("bytes="):
                spec = header[6:].split("-")
                if spec[0]:
                    start = int(spec[0])
                if len(spec) > 1 and spec[1]:
                    end = int(spec[1])
                partial = True
            end = min(end, size - 1)
            length = max(0, end - start + 1)

            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            with open(path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    remaining -= len(chunk)

    return Handler


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """소스 영상을 로컬 HTTP로 내려주는 서버. yt-dlp가 이 URL로 받아간다."""
    root = tmp_path_factory.mktemp("serve")
    _build_source(root / "source.mp4")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), _range_handler(root))
    server.allow_reuse_address = True
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/source.mp4"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def allow_any_url(monkeypatch):
    """이 파일의 테스트에서는 유튜브 URL 검사를 건너뛴다."""
    monkeypatch.setattr(downloader, "normalize_url", lambda url: url)


def test_fetch_range_downloads_only_the_requested_span(served, tmp_path):
    path = downloader.fetch_range(served, tmp_path, "clip", start=20.0, end=30.0)
    assert path.exists()

    from app import media

    info = media.probe(path)
    assert info.duration == pytest.approx(10.0, abs=0.6), "받은 길이가 요청 구간과 달라졌습니다"
    assert info.has_video and info.has_audio, "영상과 소리가 함께 있어야 합니다"


def test_downloaded_clip_starts_exactly_at_the_requested_time(served, tmp_path):
    """여유분 계산이 기대는 전제: 파일의 0초 = 소스의 start초."""
    path = downloader.fetch_range(served, tmp_path, "clip", start=20.0, end=30.0)
    # 20~30초 구간은 통째로 파란색 블록이다
    assert _nearest_color(_sample_color(path, 0.5)) == "blue"
    assert _nearest_color(_sample_color(path, 9.0)) == "blue"


def test_clip_spanning_two_blocks_keeps_the_boundary_in_place(served, tmp_path):
    """25~35초를 받으면 5초 지점에서 파랑 -> 노랑으로 바뀌어야 한다."""
    path = downloader.fetch_range(served, tmp_path, "clip2", start=25.0, end=35.0)
    assert _nearest_color(_sample_color(path, 1.0)) == "blue"
    assert _nearest_color(_sample_color(path, 8.0)) == "yellow"


def test_whole_video_when_no_range_given(served, tmp_path):
    from app import media

    path = downloader.fetch_range(served, tmp_path, "full")
    assert media.probe(path).duration == pytest.approx(60.0, abs=1.0)


def test_full_download_reports_progress(served, tmp_path):
    seen = []
    downloader.fetch_range(served, tmp_path, "prog", on_progress=seen.append)
    assert seen, "전체를 받을 때는 진행률이 와야 합니다"
    assert 0.0 <= min(seen) <= max(seen) <= 1.0


def test_section_download_may_not_report_progress(served, tmp_path):
    """구간만 받을 때 yt-dlp는 ffmpeg에 맡기고, ffmpeg은 바이트 진행률을 돌려주지 않는다.

    그래서 진행률이 아예 안 올 수 있다. 화면은 이 경우를 '진행률 표시 없음'으로
    처리해야 하며(projects.Task.indeterminate), 0%에 멈춘 것처럼 보이면 안 된다.
    """
    seen = []
    downloader.fetch_range(served, tmp_path, "prog2", start=10.0, end=20.0, on_progress=seen.append)
    assert all(0.0 <= value <= 1.0 for value in seen)


def test_cancel_stops_the_download(served, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(downloader.Cancelled):
        downloader.fetch_range(served, tmp_path, "cancel", start=0.0, end=60.0, cancel=cancel)


# --- 오류 메시지 ------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ERROR: [youtube] X: Sign in to confirm you're not a bot", "봇으로 판단"),
        ("ERROR: [youtube] X: Unable to download API page: <urlopen error "
         "Tunnel connection failed: 403 Forbidden> (caused by ProxyError('x'))", "프록시/방화벽"),
        ("ERROR: [youtube] X: Private video. Sign in if you've been granted access", "비공개"),
        ("ERROR: [youtube] X: Video unavailable", "볼 수 없는"),
        ("ERROR: Requested format is not available", "화질을 낮춰"),
        ("ERROR: [youtube] X: This video is available to this channel's members only", "멤버십"),
        ("ERROR: [youtube] X: Sign in to confirm your age", "연령 제한"),
    ],
)
def test_error_messages_are_actionable(raw, expected):
    assert expected in downloader._friendly(Exception(raw))


def test_error_message_drops_yt_dlp_boilerplate():
    raw = ("ERROR: [youtube] X: something new broke; please report this issue on "
           "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template")
    message = downloader._friendly(Exception(raw))
    assert "please report" not in message.lower()
    assert "something new broke" in message


# --- 진단 도구 --------------------------------------------------------------

def test_doctor_passes_every_step_on_a_reachable_video(served, tmp_path, monkeypatch):
    """유튜브 대신 로컬 서버로 진단 전 과정을 돌려 성공 화면을 확인한다."""
    from app import doctor

    monkeypatch.setattr(doctor.downloader, "normalize_url", lambda url: url)
    monkeypatch.setattr(
        doctor.downloader, "probe_url",
        lambda url: downloader.VideoInfo(
            video_id="local", url=url, title="로컬 시험 영상", duration=60.0,
            thumbnail="", uploader="테스트", is_live=False,
        ),
    )

    report = doctor.run_checks(served, height=720, workdir=tmp_path)
    text = doctor.render_report(report)

    assert report.ok, text
    names = [c.name for c in report.checks]
    assert names[0] == "ffmpeg 설치" and names[-1] == "잘라 이어붙이기"
    assert not any(c.skipped for c in report.checks)
    assert "모든 단계 통과" in text
    assert "로컬 시험 영상" in text


def test_doctor_stops_and_explains_on_a_bad_link(tmp_path, monkeypatch):
    from app import doctor

    monkeypatch.setattr(doctor.downloader, "normalize_url", REAL_NORMALIZE_URL)
    report = doctor.run_checks("https://vimeo.com/123", workdir=tmp_path)
    text = doctor.render_report(report)

    assert not report.ok
    failed = [c for c in report.checks if not c.ok and not c.skipped]
    assert failed[0].name == "링크 해석"
    assert "유튜브 링크만" in failed[0].detail
    # 뒷단계는 실행하지 않고 건너뛴다
    assert [c.name for c in report.checks if c.skipped] == [
        "영상 정보 조회", "구간 다운로드", "받은 파일 확인", "잘라 이어붙이기"
    ]
    assert "복사해 알려주세요" in text


# --- 자바스크립트 런타임 ------------------------------------------------------

def test_available_js_runtimes_only_reports_installed_ones(monkeypatch):
    monkeypatch.setattr(downloader.shutil, "which",
                        lambda name: "/usr/bin/node" if name == "node" else None)
    assert downloader.available_js_runtimes() == {"node": {"path": "/usr/bin/node"}}


def test_installed_runtimes_are_passed_to_yt_dlp(monkeypatch):
    """yt-dlp 기본값은 deno만 본다. 설치된 걸 찾아 넘겨야 챌린지를 풀 수 있다."""
    monkeypatch.setattr(downloader.shutil, "which",
                        lambda name: "/usr/bin/bun" if name == "bun" else None)
    assert downloader._base_opts()["js_runtimes"] == {"bun": {"path": "/usr/bin/bun"}}


def test_no_runtime_leaves_yt_dlp_default_alone(monkeypatch):
    monkeypatch.setattr(downloader.shutil, "which", lambda name: None)
    assert "js_runtimes" not in downloader._base_opts()
