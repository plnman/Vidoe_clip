"""브라우저 점검용 가짜 서버.

유튜브 대신 합성 영상을 만들어 준다. 실제 서버·실제 ffmpeg을 그대로 쓰므로
화면 동작을 사람 손 없이 확인할 수 있다.

    python tests/fake_server.py [포트]
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, downloader, projects  # noqa: E402

SOURCE_DURATION = 300.0


def _synth(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", "-t", str(seconds), str(path)],
        check=True, capture_output=True,
    )
    return path


def fake_probe(url: str) -> downloader.VideoInfo:
    return downloader.VideoInfo(
        video_id="dQw4w9WgXcQ",
        url=downloader.normalize_url(url),
        title="합성 테스트 영상",
        duration=SOURCE_DURATION,
        thumbnail="",
        uploader="테스트 채널",
        is_live=False,
    )


def fake_fetch(url, dest_dir, name, *, start=None, end=None, on_progress=None, **kwargs):
    dest_dir.mkdir(parents=True, exist_ok=True)
    length = SOURCE_DURATION if start is None else (end - start)
    # CLIPPER_FAKE_SILENT=1 이면 진행률을 주지 않는다 — 실제 구간 다운로드와 같은 상황
    if on_progress and not os.environ.get("CLIPPER_FAKE_SILENT"):
        for step in (0.3, 0.7, 1.0):
            on_progress(step)
    return _synth(dest_dir / f"{name}.mp4", round(length, 2))


def main() -> None:
    import uvicorn

    config.WORK_DIR = Path(tempfile.mkdtemp(prefix="clipper-fake-"))
    projects.config.WORK_DIR = config.WORK_DIR
    downloader.probe_url = fake_probe
    projects.downloader.probe_url = fake_probe
    projects.downloader.fetch_range = fake_fetch

    from app.main import app

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
