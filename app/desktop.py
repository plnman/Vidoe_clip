"""데스크톱 앱 진입점.

로컬 서버를 띄우고 창을 하나 연다. 화면은 웹앱과 같은 것을 그대로 쓴다.
창을 닫으면 서버도 함께 내려간다.

    python -m app.desktop            # 개발 중 실행
    YoutubeClipper(.exe)             # 묶어서 배포했을 때
"""

from __future__ import annotations

import argparse
import io
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "유튜브 구간 편집기"
STARTUP_TIMEOUT = 30.0


def log_path() -> Path:
    from . import config

    return config.user_data_dir() / "app.log"


def ensure_streams() -> Path | None:
    """`sys.stdout`/`stderr`가 None이면 로그 파일로 바꿔 끼운다.

    창 모드로 묶은 앱(console=False)을 **콘솔 없이** 실행하면 이 둘이 None이 된다.
    바탕화면 아이콘으로 누를 때가 정확히 그 경우다. 그러면 uvicorn이 로깅을 준비하다
    `sys.stdout.isatty()`에서 죽고, 서버가 아예 뜨지 않는다. 창도 안 뜨고 오류도 안 보이는
    "눌러도 아무 일이 없는" 증상이 여기서 나온다.

    셸에서 실행하면 콘솔 핸들을 물려받아 멀쩡하다 — 그래서 개발 중에는 드러나지 않는다.

    바꿔 끼우는 김에 로그도 남긴다. 사용자 PC에서 무슨 일이 있었는지 볼 방법이 이것뿐이다.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None

    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "a", encoding="utf-8", buffering=1)
        stream.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 시작 =====\n")
    except OSError:
        # 로그조차 못 쓰는 상황이어도 앱은 떠야 한다
        path, stream = None, io.StringIO()

    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return path


def alert(message: str) -> None:
    """창 모드에서는 콘솔에 찍어봐야 아무도 못 본다. 보이는 곳에 띄운다."""
    print(message, file=sys.stderr, flush=True)
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        pass


def free_port(preferred: int = 0) -> int:
    """쓸 수 있는 포트 하나. 고정 포트를 쓰면 두 번째 실행이 충돌한다."""
    if preferred:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", preferred)) != 0:
                return preferred
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(port: int, server_box: list, error_box: list | None = None) -> None:
    import uvicorn

    from . import config as settings

    # 포트는 여기서 정해진다. 알려주지 않으면 시작 안내문이 기본값(8000)을 찍는다.
    settings.PORT = port
    settings.HOST = "127.0.0.1"

    try:
        from .main import app

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        server_box.append(server)
        server.run()
    except BaseException as exc:
        # 스레드에서 죽으면 아무도 모른 채 30초를 기다리다 조용히 끝난다.
        # 무엇 때문인지 부른 쪽에 넘겨서 사용자에게 보여줄 수 있게 한다.
        import traceback

        traceback.print_exc()
        if error_box is not None:
            error_box.append(exc)
        raise


def wait_until_ready(port: int, timeout: float = STARTUP_TIMEOUT, error_box: list | None = None) -> bool:
    """서버가 응답할 때까지 기다린다. 창을 먼저 열면 빈 화면이 보인다."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        # 서버가 이미 죽었으면 남은 시간을 기다릴 이유가 없다
        if error_box:
            return False
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
    """네이티브 창으로 연다. 못 열면 False를 돌려줘 브라우저로 넘어가게 한다.

    ImportError만 잡으면 안 된다. pywebview가 깔려 있어도 창이 못 뜨는 경우가 있다 —
    윈도우에 WebView2 런타임이 없거나, 리눅스에 WebKitGTK가 없을 때가 그렇다.
    그때 예외가 그대로 올라오면 앱이 죽는다. 브라우저로라도 열어주는 편이 낫다.
    """
    global _window
    try:
        import webview
    except ImportError:
        return False

    try:
        _window = webview.create_window(APP_NAME, url, width=1100, height=880, min_size=(720, 600))
        webview.start()
        return True
    except Exception as exc:
        print(f"앱 창을 열지 못해 브라우저로 엽니다: {exc}", file=sys.stderr, flush=True)
        return False
    finally:
        _window = None


def open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="YoutubeClipper", description=APP_NAME)
    parser.add_argument("--port", type=int, default=0, help="쓸 포트 (기본: 비어 있는 것 자동 선택)")
    parser.add_argument("--browser", action="store_true", help="앱 창 대신 기본 브라우저로 열기")
    parser.add_argument("--no-open", action="store_true", help="창을 열지 않고 서버만 띄우기")
    args = parser.parse_args(argv)

    # 무엇보다 먼저. 이게 없으면 콘솔 없이 실행됐을 때 uvicorn이 로깅에서 죽는다.
    logfile = ensure_streams()

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    server_box: list = []
    error_box: list = []
    thread = threading.Thread(target=_serve, args=(port, server_box, error_box), daemon=True)
    thread.start()

    if not wait_until_ready(port, error_box=error_box):
        reason = f"\n\n원인: {error_box[0]}" if error_box else ""
        where = f"\n\n자세한 내용: {logfile}" if logfile else ""
        alert(f"서버를 시작하지 못했습니다.{reason}{where}")
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
