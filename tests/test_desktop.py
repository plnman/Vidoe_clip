"""데스크톱 진입점. 창을 띄우는 부분은 빼고 서버 기동까지 확인한다."""

import socket
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
