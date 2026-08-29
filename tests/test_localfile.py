"""내 컴퓨터의 영상 파일을 소스로 쓰는 흐름.

유튜브 소스와 달리 받을 것이 없다. 이 파일이 확인하는 것은 두 가지다.
 - 파일을 열면 곧바로 전 구간이 편집 가능해진다(다운로더를 부르지 않는다)
 - 경로로 연 파일은 복사되지 않고, 프로젝트를 지워도 원본이 남는다
"""

import subprocess

import pytest
from fastapi.testclient import TestClient

from app import config, downloader, projects
from app.main import app

SOURCE_SECONDS = 30


def _synth(path, seconds=SOURCE_SECONDS, size="320x240", with_audio=True):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", str(seconds)]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(projects.config, "WORK_DIR", tmp_path / "work")

    # 파일 소스가 다운로더를 부르면 그 자리에서 실패하게 해 둔다.
    def forbidden(*args, **kwargs):
        raise AssertionError("파일 소스인데 다운로드를 시도했습니다")

    monkeypatch.setattr(projects.downloader, "fetch_range", forbidden)
    monkeypatch.setattr(projects.downloader, "probe_url", forbidden)

    # 파일 경로 입력은 서버를 켠 그 PC에서만 허용된다. 그 조건을 만들어 준다.
    with TestClient(app, client=("127.0.0.1", 41000)) as test_client:
        yield test_client


@pytest.fixture
def video(tmp_path):
    return _synth(tmp_path / "내 영상.mp4")


def open_local(client, path):
    return client.post("/api/projects/local", json={"path": str(path)})


def wait(client, project_id, timeout=120):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/projects/{project_id}").json()
        if body["task"]["status"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("작업이 끝나지 않았습니다")


# --- 열기 ------------------------------------------------------------------

def test_open_by_path(client, video):
    body = open_local(client, video).json()
    assert body["source"] == "file"
    assert body["video"]["title"] == "내 영상"
    assert body["video"]["duration"] == pytest.approx(SOURCE_SECONDS, abs=0.5)
    # 썸네일은 파일에서 뽑아 자기 자신을 가리킨다
    assert body["video"]["thumbnail"].startswith(f"/api/projects/{body['id']}/clips/")


def test_open_by_path_does_not_copy(client, video):
    before = video.stat()
    body = open_local(client, video).json()
    project = projects.store.get(body["id"])
    clip = next(iter(project.clips.values()))
    assert clip.path == video

    # 프로젝트를 지워도 원본은 그대로여야 한다
    assert client.delete(f"/api/projects/{body['id']}").status_code == 200
    assert video.exists() and video.stat().st_size == before.st_size


def test_upload_takes_ownership(client, video):
    with video.open("rb") as handle:
        res = client.post("/api/projects/upload", files={"file": ("올린영상.mp4", handle, "video/mp4")})
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["source"] == "file"
    assert body["video"]["title"] == "올린영상"

    project = projects.store.get(body["id"])
    clip = next(iter(project.clips.values()))
    # 올린 파일은 프로젝트 폴더 안에 있다(임시 폴더에 남지 않는다)
    assert clip.path.parent == project.dir
    assert not list((config.WORK_DIR / "_uploads").glob("*"))


def test_audio_only_file_opens(client, tmp_path):
    audio = tmp_path / "소리만.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-c:a", "aac", "-t", "10", str(audio)],
        check=True, capture_output=True,
    )
    body = open_local(client, audio).json()
    assert body["source"] == "file"
    assert "소리만" in body["video"]["uploader"]


# --- 거절 ------------------------------------------------------------------

def test_rejects_missing_file(client, tmp_path):
    res = open_local(client, tmp_path / "없는영상.mp4")
    assert res.status_code == 400
    assert "찾을 수 없습니다" in res.json()["detail"]


def test_rejects_non_media_suffix(client, tmp_path):
    doc = tmp_path / "메모.txt"
    doc.write_text("영상이 아님", encoding="utf-8")
    res = open_local(client, doc)
    assert res.status_code == 400
    assert "아닙니다" in res.json()["detail"]


def test_rejects_unreadable_file(client, tmp_path):
    fake = tmp_path / "깨진영상.mp4"
    fake.write_bytes(b"not a video at all")
    res = open_local(client, fake)
    assert res.status_code == 400
    assert "읽지 못했습니다" in res.json()["detail"]


def test_path_route_is_loopback_only(tmp_path, monkeypatch, video):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(projects.config, "WORK_DIR", tmp_path / "work")
    # 다른 기기에서 접속한 상황. 서버 PC의 아무 파일이나 열 수 있으면 안 된다.
    with TestClient(app, client=("192.168.0.50", 41000)) as remote:
        res = remote.post("/api/projects/local", json={"path": str(video)})
        assert res.status_code == 403
        assert remote.get("/api/health").json()["local_files"] is False


# --- 편집·렌더 -------------------------------------------------------------

def test_prepare_is_instant_and_covers_everything(client, video):
    project = open_local(client, video).json()
    pid = project["id"]

    # 파일의 끝쪽 구간까지 포함해서 — 유튜브였다면 새로 받아야 할 범위다
    res = client.post(f"/api/projects/{pid}/segments", json={"text": "0:02-0:05 앞\n0:25-0:29 뒤"})
    assert res.status_code == 200, res.text
    body = res.json()["project"]
    assert body["pending"] == 0
    assert all(cut["ready"] for cut in body["cuts"])

    client.post(f"/api/projects/{pid}/prepare")
    body = wait(client, pid)
    assert body["task"]["status"] == "done"
    assert body["pending"] == 0


def test_render_from_local_file(client, video):
    project = open_local(client, video).json()
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:02-0:05 앞\n0:20-0:24 뒤"})

    res = client.post(f"/api/projects/{pid}/render", json={"format": "mp4", "quality": "fast"})
    assert res.status_code == 200, res.text
    body = wait(client, pid)
    assert body["task"]["status"] == "done", body["task"]
    assert body["result"]["name"].endswith(".mp4")

    from app import media

    result = projects.store.get(pid).result
    assert media.probe(result).duration == pytest.approx(7.0, abs=0.4)


def test_edit_after_render_needs_no_redownload(client, video):
    """유튜브 소스의 '여유분' 개념이 파일에서는 영상 전체로 넓어진다."""
    project = open_local(client, video).json()
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:10-0:12 하나"})
    cuts = client.get(f"/api/projects/{pid}").json()["cuts"]

    # 여유분(기본 10초)을 한참 넘겨 옮겨도 그대로 준비된 상태여야 한다
    moved = [{"id": cuts[0]["id"], "start": 0.5, "end": 28.0, "title": "하나", "enabled": True}]
    body = client.patch(f"/api/projects/{pid}/cuts", json={"cuts": moved}).json()
    assert body["pending"] == 0
    assert body["cuts"][0]["ready"] is True

    client.post(f"/api/projects/{pid}/render", json={"format": "mp4", "quality": "fast"})
    assert wait(client, pid)["task"]["status"] == "done"


def test_source_is_downloadable_as_clip_media(client, video):
    project = open_local(client, video).json()
    clip_id = project["cuts"] and None  # 구간을 넣기 전에는 cuts가 비어 있다
    pid = project["id"]
    stored = projects.store.get(pid)
    clip_id = next(iter(stored.clips))
    res = client.get(f"/api/projects/{pid}/clips/{clip_id}/media")
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
    assert client.get(f"/api/projects/{pid}/clips/{clip_id}/poster").status_code == 200
