"""yt-dlp 연동을 실제 yt-dlp로 검증한다.

유튜브에는 붙지 않는다. 대신 로컬 HTTP 서버가 내려주는 영상에 대해
'구간만 받기'를 실제로 돌려서, 받은 파일이 요청한 시각에서 정확히 시작하는지 본다.
이 전제가 깨지면 여유분(pad) 계산이 통째로 어긋나므로 가장 중요한 확인이다.
"""

import functools
import http.server
import socket
import socketserver
import subprocess
import threading

import pytest

from app import downloader

# 10초씩 색이 바뀌는 60초 영상. 색을 보면 소스의 몇 초 지점인지 알 수 있다.
COLORS = ["red", "green", "blue", "yellow", "magenta", "cyan"]
RGB = {
    "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "magenta": (255, 0, 255), "cyan": (0, 255, 255),
}
BLOCK = 10


def _build_source(path):
    """색 블록 6개를 이어붙인 60초짜리 mp4."""
    inputs, filters = [], []
    for i, color in enumerate(COLORS):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=10:duration={BLOCK}"]
        filters.append(f"[{i}:v]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", "".join(filters) + f"concat=n={len(COLORS)}:v=1:a=0[v]",
           "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
           "-g", "20", "-pix_fmt", "yuv420p", str(path)]
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


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """소스 영상을 로컬 HTTP로 내려주는 서버. yt-dlp가 이 URL로 받아간다."""
    root = tmp_path_factory.mktemp("serve")
    _build_source(root / "source.mp4")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = socketserver.TCPServer(("127.0.0.1", port), handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/source.mp4"
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def allow_any_url(monkeypatch):
    """이 테스트에서만 유튜브 URL 검사를 건너뛴다."""
    monkeypatch.setattr(downloader, "normalize_url", lambda url: url)


def test_fetch_range_downloads_only_the_requested_span(served, tmp_path):
    path = downloader.fetch_range(served, tmp_path, "clip", start=20.0, end=30.0)
    assert path.exists()

    from app import media

    info = media.probe(path)
    assert info.duration == pytest.approx(10.0, abs=0.6), "받은 길이가 요청 구간과 달라졌습니다"


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
