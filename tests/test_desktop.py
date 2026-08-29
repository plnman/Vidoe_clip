"""데스크톱 진입점. 창을 띄우는 부분은 빼고 서버 기동까지 확인한다."""

import socket
import sys
import threading
import time

from app import desktop


def test_free_port_returns_something_bindable():
    port = desktop.free_port()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))  # 예외가 나면 실패


def test_free_port_avoids_a_port_already_in_use():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy = taken.getsockname()[1]
        assert desktop.free_port(busy) != busy


def test_free_port_uses_the_preferred_one_when_it_is_open():
    port = desktop.free_port()
    assert desktop.free_port(port) == port


def test_wait_until_ready_gives_up_on_a_dead_port():
    start = time.monotonic()
    assert desktop.wait_until_ready(desktop.free_port(), timeout=0.5) is False
    assert time.monotonic() - start < 3.0


def test_server_starts_and_answers():
    """--no-open 경로: 창 없이 서버만 뜨는지."""
    port = desktop.free_port()
    box: list = []
    thread = threading.Thread(target=desktop._serve, args=(port, box), daemon=True)
    thread.start()
    try:
        assert desktop.wait_until_ready(port, timeout=20), "서버가 응답하지 않았습니다"
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as res:
            assert res.status == 200
    finally:
        for server in box:
            server.should_exit = True
        thread.join(timeout=10)


def test_open_window_reports_failure_without_pywebview(monkeypatch):
    """pywebview가 없으면 False를 돌려줘 브라우저로 넘어가야 한다."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            raise ImportError("no pywebview")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert desktop.open_window("http://127.0.0.1:1") is False


# --- 묶어서 배포했을 때 (frozen) --------------------------------------------

def test_bundled_bin_goes_to_front_of_path(tmp_path, monkeypatch):
    """yt-dlp는 '구간만 받기'가 가능한지 볼 때 PATH만 본다. 옵션으로는 부족하다."""
    from app import config

    bundled = tmp_path / "bin"
    bundled.mkdir()
    monkeypatch.setattr(config, "bundled_bin_dir", lambda: bundled)
    monkeypatch.setenv("PATH", "/usr/bin")

    config.use_bundled_bin()
    import os

    assert os.environ["PATH"].split(os.pathsep)[0] == str(bundled)


def test_bundled_bin_is_not_added_twice(tmp_path, monkeypatch):
    from app import config

    bundled = tmp_path / "bin"
    bundled.mkdir()
    monkeypatch.setattr(config, "bundled_bin_dir", lambda: bundled)
    monkeypatch.setenv("PATH", "/usr/bin")

    config.use_bundled_bin()
    config.use_bundled_bin()
    import os

    assert os.environ["PATH"].split(os.pathsep).count(str(bundled)) == 1


def test_nothing_happens_when_not_bundled(monkeypatch):
    """개발 중에는 건드리지 않는다."""
    from app import config

    monkeypatch.setattr(config, "bundled_bin_dir", lambda: None)
    monkeypatch.setenv("PATH", "/usr/bin")
    config.use_bundled_bin()
    import os

    assert os.environ["PATH"] == "/usr/bin"


# --- 콘솔 없이 실행될 때 (창 모드로 묶었을 때의 실제 조건) --------------------

def test_server_starts_without_stdout(tmp_path, monkeypatch):
    """창 모드 앱을 바탕화면에서 실행하면 sys.stdout이 None이다.

    그 상태로 두면 uvicorn이 로깅을 준비하다 `sys.stdout.isatty()`에서 죽어
    서버가 아예 뜨지 않는다 — 눌러도 아무 일이 없는 그 증상이다.
    셸에서 띄우면 콘솔 핸들을 물려받아 멀쩡하므로 개발 중에는 드러나지 않는다.
    """
    from app import config, desktop

    monkeypatch.setattr(config, "user_data_dir", lambda: tmp_path / "YoutubeClipper")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    desktop.ensure_streams()
    assert sys.stdout is not None and sys.stderr is not None

    port = desktop.free_port()
    box: list = []
    errors: list = []
    thread = threading.Thread(target=desktop._serve, args=(port, box, errors), daemon=True)
    thread.start()
    try:
        assert desktop.wait_until_ready(port, timeout=20, error_box=errors), (
            f"서버가 뜨지 않았습니다: {errors}"
        )
    finally:
        for server in box:
            server.should_exit = True
        thread.join(timeout=10)


def test_ensure_streams_writes_a_log_file(tmp_path, monkeypatch):
    """사용자 PC에서 무슨 일이 있었는지 볼 방법은 이 로그뿐이다."""
    from app import config, desktop

    monkeypatch.setattr(config, "user_data_dir", lambda: tmp_path / "YoutubeClipper")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    path = desktop.ensure_streams()
    print("남는지 확인")
    sys.stdout.flush()
    assert path is not None and path.exists()
    assert "남는지 확인" in path.read_text(encoding="utf-8")


def test_ensure_streams_leaves_real_streams_alone(tmp_path, monkeypatch):
    from app import config, desktop

    monkeypatch.setattr(config, "user_data_dir", lambda: tmp_path / "YoutubeClipper")
    before = sys.stdout
    assert desktop.ensure_streams() is None
    assert sys.stdout is before


def test_open_window_falls_back_when_the_window_cannot_open(monkeypatch):
    """WebView2가 없는 PC 등. 예외가 올라오면 앱이 죽으니 브라우저로 넘겨야 한다."""
    from app import desktop

    class FakeWebview:
        OPEN_DIALOG = 10

        def create_window(self, *args, **kwargs):
            return object()

        def start(self):
            raise RuntimeError("WebView2 런타임을 찾을 수 없습니다")

    monkeypatch.setitem(sys.modules, "webview", FakeWebview())
    assert desktop.open_window("http://127.0.0.1:1") is False
    assert desktop.picker_available() is False
