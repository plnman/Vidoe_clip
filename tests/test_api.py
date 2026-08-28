"""유튜브 접속 없이 전체 흐름을 검증한다.

다운로더만 합성 영상 생성기로 바꿔치기하고, 파싱·준비·편집·렌더·다운로드를
실제 서버와 실제 ffmpeg으로 돌린다.
"""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import config, downloader, projects
from app.main import app

SOURCE_DURATION = 300.0


def _synth(path, seconds, size="320x240"):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", "-t", str(seconds), str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(projects.config, "WORK_DIR", tmp_path / "work")

    def fake_probe(url):
        downloader.normalize_url(url)  # 실제 URL 검증은 그대로 거친다
        return downloader.VideoInfo(
            video_id="dQw4w9WgXcQ",
            url=downloader.normalize_url(url),
            title="테스트 영상",
            duration=SOURCE_DURATION,
            thumbnail="",
            uploader="채널",
            is_live=False,
        )

    calls = []

    def fake_fetch(url, dest_dir, name, *, start=None, end=None, on_progress=None, **kwargs):
        calls.append((start, end))
        dest_dir.mkdir(parents=True, exist_ok=True)
        length = SOURCE_DURATION if start is None else (end - start)
        if on_progress:
            on_progress(0.5)
            on_progress(1.0)
        return _synth(dest_dir / f"{name}.mp4", round(length, 2))

    monkeypatch.setattr(downloader, "probe_url", fake_probe)
    monkeypatch.setattr(projects.downloader, "probe_url", fake_probe)
    monkeypatch.setattr(projects.downloader, "fetch_range", fake_fetch)

    with TestClient(app) as test_client:
        test_client.fetch_calls = calls
        yield test_client


def wait(client, project_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/projects/{project_id}").json()
        if body["task"]["status"] != "running":
            return body
        time.sleep(0.15)
    raise AssertionError("작업이 끝나지 않았습니다")


def make_project(client, url="https://youtu.be/dQw4w9WgXcQ"):
    res = client.post("/api/projects", json={"url": url})
    assert res.status_code == 200, res.text
    return res.json()


# --- 기본 ------------------------------------------------------------------

def test_health(client):
    body = client.get("/api/health").json()
    assert body["ffmpeg"] is True
    assert body["defaults"]["pad"] == config.DEFAULT_PAD
    assert body["default_format"] == "mp4"
    assert {"mp4", "webm", "gif", "mp3"} <= set(body["formats"])


def test_index_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "구간 편집기" in res.text


def test_parse_endpoint(client):
    body = client.post("/api/parse", json={"text": "0:10-0:20 가\n0:30-0:40 나"}).json()
    assert len(body["segments"]) == 2
    assert body["total"] == 20


def test_rejects_non_youtube_url(client):
    res = client.post("/api/projects", json={"url": "https://vimeo.com/1"})
    assert res.status_code == 400
    assert "유튜브" in res.json()["detail"]


def test_missing_project_is_404(client):
    assert client.get("/api/projects/p_nope").status_code == 404


# --- 전체 흐름 --------------------------------------------------------------

def test_full_flow_prepare_edit_render_download(client):
    project = make_project(client)
    pid = project["id"]
    assert project["video"]["duration"] == SOURCE_DURATION

    res = client.post(f"/api/projects/{pid}/segments",
                      json={"text": "0:30-0:40 인트로\n2:00-2:15 본론\n4:00-4:05 마무리"})
    assert res.status_code == 200
    project = res.json()["project"]
    assert len(project["cuts"]) == 3
    assert project["pending"] == 3
    assert project["total_duration"] == 30

    client.post(f"/api/projects/{pid}/prepare")
    project = wait(client, pid)
    assert project["task"]["status"] == "done", project["task"]
    assert project["pending"] == 0
    assert all(cut["ready"] for cut in project["cuts"])

    # 여유분(pad)만큼 앞뒤로 더 받았는지
    starts = [c[0] for c in client.fetch_calls]
    assert starts == [20.0, 110.0, 230.0]

    # 미리보기 조각을 Range 요청으로 받을 수 있는지
    clip_id = project["cuts"][0]["clip_id"]
    media = client.get(f"/api/projects/{pid}/clips/{clip_id}/media", headers={"Range": "bytes=0-99"})
    assert media.status_code == 206
    assert len(media.content) == 100
    assert client.get(f"/api/projects/{pid}/clips/{clip_id}/poster").status_code == 200

    # 편집: 두 번째 구간을 여유분 안에서 늘리고, 세 번째를 뺀다
    cuts = project["cuts"]
    cuts[1]["end"] = 140.0
    cuts[2]["enabled"] = False
    project = client.patch(f"/api/projects/{pid}/cuts", json={"cuts": cuts}).json()
    assert project["pending"] == 0, "여유분 안이므로 다시 받을 필요가 없어야 한다"
    assert project["cuts"][1]["duration"] == 20  # 15초 -> 20초
    assert project["total_duration"] == 30  # 10 + 20 (세 번째는 제외)
    assert len(client.fetch_calls) == 3, "편집만으로 다시 다운로드하면 안 된다"

    res = client.post(f"/api/projects/{pid}/render", json={"quality": "fast"})
    assert res.status_code == 200
    project = wait(client, pid)
    assert project["task"]["status"] == "done", project["task"]
    assert project["result"]["size"] > 0
    assert project["result"]["name"].endswith(".mp4")

    download = client.get(f"/api/projects/{pid}/download")
    assert download.status_code == 200
    assert len(download.content) == project["result"]["size"]
    assert len(client.fetch_calls) == 3, "렌더가 다시 다운로드하면 안 된다"


def test_editing_beyond_pad_marks_cut_stale_and_refetches(client):
    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "1:00-1:10 하나"})
    client.post(f"/api/projects/{pid}/prepare")
    project = wait(client, pid)
    assert project["cuts"][0]["room_before"] == pytest.approx(config.DEFAULT_PAD, abs=1.0)

    cuts = project["cuts"]
    cuts[0]["start"] = 10.0  # 여유분 10초를 훨씬 넘어섬
    project = client.patch(f"/api/projects/{pid}/cuts", json={"cuts": cuts}).json()
    assert project["pending"] == 1
    assert project["cuts"][0]["ready"] is False

    assert client.post(f"/api/projects/{pid}/render", json={}).status_code == 409

    client.post(f"/api/projects/{pid}/prepare")
    project = wait(client, pid)
    assert project["pending"] == 0
    assert len(client.fetch_calls) == 2


def test_overlapping_needs_are_downloaded_once(client):
    project = make_project(client)
    pid = project["id"]
    # 두 구간이 여유분까지 합치면 붙는다 -> 한 번만 받아야 한다
    client.post(f"/api/projects/{pid}/segments", json={"text": "1:00-1:10 가\n1:15-1:25 나"})
    client.post(f"/api/projects/{pid}/prepare")
    project = wait(client, pid)
    assert project["pending"] == 0
    assert len(client.fetch_calls) == 1
    assert client.fetch_calls[0] == (50.0, 95.0)


def test_whole_video_mode_downloads_once_and_covers_everything(client):
    project = make_project(client)
    pid = project["id"]
    client.patch(f"/api/projects/{pid}/options", json={"whole": True})
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:05-0:15 가\n4:30-4:40 나"})
    client.post(f"/api/projects/{pid}/prepare")
    project = wait(client, pid)
    assert project["pending"] == 0
    assert client.fetch_calls == [(None, None)]

    # 어디로 옮겨도 다시 받을 필요가 없다
    cuts = project["cuts"]
    cuts[0]["start"], cuts[0]["end"] = 200.0, 260.0
    project = client.patch(f"/api/projects/{pid}/cuts", json={"cuts": cuts}).json()
    assert project["pending"] == 0


@pytest.mark.parametrize("fmt,suffix", [("mp3", ".mp3"), ("webm", ".webm"), ("gif", ".gif")])
def test_render_formats(client, fmt, suffix):
    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:30-0:35 가"})
    client.post(f"/api/projects/{pid}/prepare")
    wait(client, pid)
    client.post(f"/api/projects/{pid}/render", json={"format": fmt})
    project = wait(client, pid)
    assert project["task"]["status"] == "done", project["task"]
    assert project["result"]["name"].endswith(suffix)
    assert project["result"]["format"] == fmt
    assert project["result"]["previewable"] is True
    assert client.get(f"/api/projects/{pid}/download").status_code == 200


def test_unknown_format_is_rejected(client):
    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:30-0:35 가"})
    client.post(f"/api/projects/{pid}/prepare")
    wait(client, pid)
    res = client.post(f"/api/projects/{pid}/render", json={"format": "avi"})
    assert res.status_code == 409
    assert "지원하지 않는" in res.json()["detail"]


def test_separate_files_are_zipped_one_per_segment(client, tmp_path):
    import io
    import zipfile

    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments",
                json={"text": "0:30-0:35 첫째\n2:00-2:04 둘째"})
    client.post(f"/api/projects/{pid}/prepare")
    wait(client, pid)

    client.post(f"/api/projects/{pid}/render", json={"format": "mp4", "separate": True})
    project = wait(client, pid)
    assert project["task"]["status"] == "done", project["task"]
    assert project["result"]["name"].endswith(".zip")
    assert project["result"]["previewable"] is False

    body = client.get(f"/api/projects/{pid}/download").content
    with zipfile.ZipFile(io.BytesIO(body)) as bundle:
        names = sorted(bundle.namelist())
    assert names == ["01_첫째.mp4", "02_둘째.mp4"]


def test_prepare_marks_progress_unknown_when_downloader_is_silent(client, monkeypatch):
    """구간 다운로드는 진행률을 안 줄 수 있다. 그때는 화면에 그렇다고 알려야 한다."""
    silent_calls = []

    def silent_fetch(url, dest_dir, name, *, start=None, end=None, on_progress=None, **kwargs):
        silent_calls.append((start, end))
        dest_dir.mkdir(parents=True, exist_ok=True)
        return _synth(dest_dir / f"{name}.mp4", round((end - start) if start is not None else 5, 2))

    monkeypatch.setattr(projects.downloader, "fetch_range", silent_fetch)

    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:30-0:35 가"})
    client.post(f"/api/projects/{pid}/prepare")

    saw_unknown = False
    for _ in range(200):
        task = client.get(f"/api/projects/{pid}").json()["task"]
        saw_unknown = saw_unknown or (task["status"] == "running" and task["indeterminate"])
        if task["status"] != "running":
            break
        time.sleep(0.02)

    project = client.get(f"/api/projects/{pid}").json()
    assert project["task"]["status"] == "done"
    assert saw_unknown, "진행률을 모르는 동안 화면에 알리지 않았습니다"
    assert project["task"]["indeterminate"] is False


def test_render_before_prepare_is_rejected(client):
    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:30-0:35 가"})
    res = client.post(f"/api/projects/{pid}/render", json={})
    assert res.status_code == 409
    assert "준비" in res.json()["detail"]


def test_segments_endpoint_rejects_unreadable_text(client):
    project = make_project(client)
    res = client.post(f"/api/projects/{project['id']}/segments", json={"text": "구간 없음"})
    assert res.status_code == 400


def test_cuts_reject_backwards_range(client):
    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:30-0:40 가"})
    cuts = client.get(f"/api/projects/{pid}").json()["cuts"]
    cuts[0]["start"], cuts[0]["end"] = 40.0, 30.0
    assert client.patch(f"/api/projects/{pid}/cuts", json={"cuts": cuts}).status_code == 400


def test_delete_project_removes_files(client):
    project = make_project(client)
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:30-0:35 가"})
    client.post(f"/api/projects/{pid}/prepare")
    wait(client, pid)
    directory = projects.store.get(pid).dir
    assert directory.exists()
    client.delete(f"/api/projects/{pid}")
    assert not directory.exists()
    assert client.get(f"/api/projects/{pid}").status_code == 404
