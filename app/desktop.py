"""데스크톱 앱 진입점.

로컬 서버를 띄우고 창을 하나 연다. 화면은 웹앱과 같은 것을 그대로 쓴다.
창을 닫으면 서버도 함께 내려간다.

    python -m app.desktop            # 개발 중 실행
    YoutubeClipper(.exe)             # 묶어서 배포했을 때
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

APP_NAME = "유튜브 구간 편집기"
STARTUP_TIMEOUT = 30.0


def free_port(preferred: int = 0) -> int:
    """쓸 수 있는 포트 하나. 고정 포트를 쓰면 두 번째 실행이 충돌한다."""
    if preferred:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", preferred)) != 0:
                return preferred
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(port: int, server_box: list) -> None:
    import uvicorn

    from . import config as settings

    # 포트는 여기서 정해진다. 알려주지 않으면 시작 안내문이 기본값(8000)을 찍는다.
    settings.PORT = port
    settings.HOST = "127.0.0.1"

    from .main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_box.append(server)
    server.run()


def wait_until_ready(port: int, timeout: float = STARTUP_TIMEOUT) -> bool:
    """서버가 응답할 때까지 기다린다. 창을 먼저 열면 빈 화면이 보인다."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


# 앱 창. 파일 선택 대화상자를 열려면 창 객체가 필요해서 들고 있는다.
# 브라우저로 열렸거나 서버만 띄운 경우에는 None으로 남는다.
_window = None

_FILE_TYPES = (
    "영상 파일 (*.mp4;*.mov;*.mkv;*.webm;*.avi;*.wmv;*.flv;*.m4v;*.mpg;*.mpeg;*.ts;*.m2ts)",
    "소리 파일 (*.mp3;*.m4a;*.aac;*.wav;*.flac;*.opus;*.ogg)",
    "모든 파일 (*.*)",
)


def picker_available() -> bool:
    return _window is not None


def pick_video_file() -> str:
    """OS 파일 선택 창을 열고 고른 경로를 돌려준다. 취소하면 빈 문자열."""
    if _window is None:
        return ""
    import webview

    chosen = _window.create_file_dialog(
        webview.OPEN_DIALOG, allow_multiple=False, file_types=_FILE_TYPES
    )
    if not chosen:
        return ""
    return str(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)


def open_window(url: str) -> bool:
    """네이티브 창으로 연다. pywebview가 없으면 False."""
    global _window
    try:
        import webview
    except ImportError:
        return False
    _window = webview.create_window(APP_NAME, url, width=1100, height=880, min_size=(720, 600))
    webview.start()
    _window = None
    return True


def open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="YoutubeClipper", description=APP_NAME)
    parser.add_argument("--port", type=int, default=0, help="쓸 포트 (기본: 비어 있는 것 자동 선택)")
    parser.add_argument("--browser", action="store_true", help="앱 창 대신 기본 브라우저로 열기")
    parser.add_argument("--no-open", action="store_true", help="창을 열지 않고 서버만 띄우기")
    args = parser.parse_args(argv)

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    server_box: list = []
    thread = threading.Thread(target=_serve, args=(port, server_box), daemon=True)
    thread.start()

    if not wait_until_ready(port):
        print("서버가 뜨지 않았습니다.", file=sys.stderr)
        return 1

    print(f"\n  {APP_NAME}\n  {url}\n", flush=True)

    try:
        if args.no_open:
            thread.join()
        elif args.browser or not open_window(url):
            # 창을 못 열면 브라우저로 대신 연다. 서버는 계속 돌려야 한다.
            open_browser(url)
            thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        for server in server_box:
            server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
