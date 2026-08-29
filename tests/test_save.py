"""완성본을 PC에 저장하는 길.

앱 창(WebView2)은 브라우저 다운로드를 기본적으로 막는다. 그래서 화면의 'PC에 저장'을
눌러도 아무 일이 없었다. 파일은 어차피 이 PC에 있으니 HTTP로 자기 자신에게
내려받을 이유도 없다 — OS 저장 창을 띄우고 그 자리로 복사한다.
"""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, desktop, projects
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(projects.config, "WORK_DIR", tmp_path / "work")
    with TestClient(app, client=("127.0.0.1", 41000)) as test_client:
        yield test_client


@pytest.fixture
def rendered(client, tmp_path):
    """결과물이 만들어져 있는 프로젝트."""
    source = tmp_path / "원본.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=6",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", "6", str(source)],
        check=True, capture_output=True,
    )
    project = client.post("/api/projects/local", json={"path": str(source)}).json()
    pid = project["id"]
    client.post(f"/api/projects/{pid}/segments", json={"text": "0:01-0:04 하나"})
    client.post(f"/api/projects/{pid}/render", json={"format": "mp4", "quality": "fast"})

    import time

    deadline = time.time() + 90
    while time.time() < deadline:
        body = client.get(f"/api/projects/{pid}").json()
        if body["task"]["status"] != "running":
            break
        time.sleep(0.1)
    assert body["task"]["status"] == "done", body["task"]
    return pid


# --- 저장 -------------------------------------------------------------------

def test_save_copies_the_result_to_the_chosen_path(client, rendered, tmp_path, monkeypatch):
    target = tmp_path / "내보낸 영상.mp4"
    monkeypatch.setattr(desktop, "picker_available", lambda: True)
    monkeypatch.setattr(desktop, "pick_save_path", lambda filename: str(target))

    body = client.post(f"/api/projects/{rendered}/save").json()
    assert body["saved"] is True
    assert body["path"] == str(target)
    assert target.exists() and target.stat().st_size > 0

    # 원본(작업 폴더의 결과물)은 그대로 남는다 — 다시 저장할 수 있어야 한다
    assert projects.store.get(rendered).result.exists()


def test_save_adds_the_extension_when_the_user_removed_it(client, rendered, tmp_path, monkeypatch):
    """저장 창에서 확장자를 지우고 저장하는 일이 흔하다. 그대로 두면 열리지 않는다."""
    monkeypatch.setattr(desktop, "picker_available", lambda: True)
    monkeypatch.setattr(desktop, "pick_save_path", lambda filename: str(tmp_path / "확장자없음"))

    body = client.post(f"/api/projects/{rendered}/save").json()
    assert body["path"].endswith(".mp4")
    assert Path(body["path"]).exists()


def test_cancelling_the_dialog_is_not_an_error(client, rendered, monkeypatch):
    monkeypatch.setattr(desktop, "picker_available", lambda: True)
    monkeypatch.setattr(desktop, "pick_save_path", lambda filename: "")

    res = client.post(f"/api/projects/{rendered}/save")
    assert res.status_code == 200
    assert res.json() == {"saved": False, "path": ""}


def test_save_needs_the_app_window(client, rendered, monkeypatch):
    """브라우저에는 저장 창이 없다. 그쪽은 평범한 다운로드 링크를 쓴다."""
    monkeypatch.setattr(desktop, "picker_available", lambda: False)
    res = client.post(f"/api/projects/{rendered}/save")
    assert res.status_code == 409


def test_save_before_rendering_says_so(client, tmp_path, monkeypatch):
    source = tmp_path / "아직.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-t", "3", str(source)],
        check=True, capture_output=True,
    )
    pid = client.post("/api/projects/local", json={"path": str(source)}).json()["id"]
    monkeypatch.setattr(desktop, "picker_available", lambda: True)
    res = client.post(f"/api/projects/{pid}/save")
    assert res.status_code == 404
    assert "결과물이 없습니다" in res.json()["detail"]


# --- 폴더 열기 --------------------------------------------------------------

def test_reveal_opens_an_existing_file(client, tmp_path, monkeypatch):
    target = tmp_path / "저장본.mp4"
    target.write_bytes(b"x")
    opened: list = []
    monkeypatch.setattr(desktop, "reveal", lambda path: opened.append(path) or True)

    body = client.post("/api/reveal", json={"path": str(target)}).json()
    assert body["opened"] is True
    assert opened == [target]


def test_reveal_refuses_a_path_that_is_not_there(client, tmp_path):
    res = client.post("/api/reveal", json={"path": str(tmp_path / "없는파일.mp4")})
    assert res.status_code == 404


def test_reveal_is_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    target = tmp_path / "저장본.mp4"
    target.write_bytes(b"x")
    with TestClient(app, client=("192.168.0.50", 41000)) as remote:
        assert remote.post("/api/reveal", json={"path": str(target)}).status_code == 403
