"""yt-dlp 갱신 경로.

묶어서 배포하면 yt-dlp도 함께 얼어붙는다. 유튜브가 규칙을 바꾸는 순간 앱이 죽고
사용자는 새 설치 파일을 기다려야 한다(docs/DESKTOP.md 2.5). 그 사태를 막는 길이라
네트워크 없이도 검증해 둔다 — PyPI 응답과 wheel을 가짜로 만들어 쓴다.
"""

import hashlib
import io
import json
import sys
import zipfile

import pytest

from app import config, updater


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "user_data_dir", lambda: tmp_path / "YoutubeClipper")
    monkeypatch.setattr(updater.config, "user_data_dir", lambda: tmp_path / "YoutubeClipper")
    return tmp_path / "YoutubeClipper"


def _wheel_bytes(version: str = "2099.1.1") -> bytes:
    """yt_dlp 패키지가 든 최소한의 wheel."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("yt_dlp/__init__.py", "")
        bundle.writestr("yt_dlp/version.py", f'__version__ = "{version}"\n')
        bundle.writestr(f"yt_dlp-{version}.dist-info/METADATA", "Name: yt-dlp\n")
    return buffer.getvalue()


def _release(version: str, payload: bytes) -> dict:
    return {
        "info": {"version": version},
        "urls": [
            {"packagetype": "sdist", "filename": f"yt_dlp-{version}.tar.gz", "url": "https://x/sdist"},
            {
                "packagetype": "bdist_wheel",
                "filename": f"yt_dlp-{version}-py3-none-any.whl",
                "url": "https://x/wheel",
                "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
            },
        ],
    }


@pytest.fixture
def pypi(monkeypatch):
    """PyPI 조회와 내려받기를 가짜로 바꾼다."""
    state = {"version": "2099.1.1", "payload": _wheel_bytes("2099.1.1"), "downloads": 0}

    monkeypatch.setattr(
        updater, "_fetch_json", lambda url: _release(state["version"], state["payload"])
    )

    def fake_download(url, sha256, dest):
        state["downloads"] += 1
        if sha256 and hashlib.sha256(state["payload"]).hexdigest() != sha256:
            raise updater.UpdateError("받은 파일이 손상됐습니다(해시 불일치)")
        dest.write_bytes(state["payload"])

    monkeypatch.setattr(updater, "_download", fake_download)
    return state


# --- 확인 ------------------------------------------------------------------

def test_check_reports_outdated(user_dir, pypi, monkeypatch):
    monkeypatch.setattr(updater, "installed_version", lambda: "2026.08.19")
    body = updater.check()
    assert body["latest"] == "2099.1.1"
    assert body["outdated"] is True
    assert body["error"] == ""


def test_check_says_up_to_date(user_dir, pypi, monkeypatch):
    monkeypatch.setattr(updater, "installed_version", lambda: "2099.1.1")
    assert updater.check()["outdated"] is False


def test_check_survives_no_network(user_dir, monkeypatch):
    def boom(url):
        raise OSError("연결 실패")

    monkeypatch.setattr(updater, "_fetch_json", boom)
    body = updater.check()
    assert body["outdated"] is False
    assert "확인 실패" in body["error"]


# --- 갱신 ------------------------------------------------------------------

def test_update_unpacks_into_user_dir(user_dir, pypi):
    result = updater.update()
    assert result["version"] == "2099.1.1"
    assert result["restart_required"] is True

    unpacked = updater.runtime_dir() / "yt_dlp" / "version.py"
    assert unpacked.exists()
    assert '2099.1.1' in unpacked.read_text(encoding="utf-8")
    # 임시 폴더를 남기지 않는다
    assert [p.name for p in updater.runtime_dir().iterdir() if p.is_dir()] == [
        "yt_dlp", "yt_dlp-2099.1.1.dist-info",
    ]


def test_update_replaces_previous_version(user_dir, pypi):
    updater.update()
    pypi["version"] = "2099.2.2"
    pypi["payload"] = _wheel_bytes("2099.2.2")
    updater.update()

    version_file = updater.runtime_dir() / "yt_dlp" / "version.py"
    assert "2099.2.2" in version_file.read_text(encoding="utf-8")


def test_update_refuses_corrupted_download(user_dir, pypi, monkeypatch):
    def wrong_bytes(url, sha256, dest):
        dest.write_bytes(_wheel_bytes("9999.9.9"))  # 해시가 맞지 않는 내용
        raise updater.UpdateError("받은 파일이 손상됐습니다(해시 불일치)")

    monkeypatch.setattr(updater, "_download", wrong_bytes)
    with pytest.raises(updater.UpdateError):
        updater.update()
    assert not (updater.runtime_dir() / "yt_dlp").exists()


def test_update_rejects_wheel_without_ytdlp(user_dir, pypi):
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("something_else/__init__.py", "")
    pypi["payload"] = empty.getvalue()
    with pytest.raises(updater.UpdateError, match="yt_dlp가 없습니다"):
        updater.update()


def test_zero_padded_version_is_not_reported_as_outdated(user_dir, pypi, monkeypatch):
    """yt-dlp는 2026.08.19, PyPI는 2026.8.19로 적는다. 같은 버전이다."""
    pypi["version"] = "2026.8.19"
    monkeypatch.setattr(updater, "installed_version", lambda: "2026.08.19")
    body = updater.check()
    assert body["outdated"] is False, body


def test_newer_version_is_still_detected(user_dir, pypi, monkeypatch):
    pypi["version"] = "2026.9.1"
    monkeypatch.setattr(updater, "installed_version", lambda: "2026.08.19")
    assert updater.check()["outdated"] is True


def test_version_key_compares_numerically():
    assert updater._version_key("2026.08.19") == (2026, 8, 19)
    assert updater._version_key("2026.8.19") == updater._version_key("2026.08.19")
    assert updater._version_key("2026.10.1") > updater._version_key("2026.9.30")


# --- 부팅 ------------------------------------------------------------------

def test_bootstrap_puts_runtime_first(user_dir, pypi):
    updater.update()
    before = list(sys.path)
    try:
        updater.bootstrap()
        assert sys.path[0] == str(updater.runtime_dir())
    finally:
        sys.path[:] = before


def test_bootstrap_does_nothing_without_update(user_dir):
    before = list(sys.path)
    updater.bootstrap()
    assert sys.path == before


def test_bootstrap_is_idempotent(user_dir, pypi):
    updater.update()
    before = list(sys.path)
    try:
        updater.bootstrap()
        updater.bootstrap()
        assert sys.path.count(str(updater.runtime_dir())) == 1
    finally:
        sys.path[:] = before
