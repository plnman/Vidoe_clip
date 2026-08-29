"""yt-dlp를 앱과 따로 갱신한다.

이 앱은 유튜브 변화 대응을 전적으로 yt-dlp에 맡긴다(docs/DESIGN.md D8).
그런데 PyInstaller로 묶으면 yt-dlp도 같이 얼어붙어, 유튜브가 규칙을 바꾸는 순간
앱이 죽고 사용자는 새 설치 파일을 기다려야 한다. 그래서 사용자 폴더에 갱신본을 두고
앱 시작 때 그쪽을 먼저 import 하도록 길을 하나 더 낸다.

    runtime/            <- 여기에 받은 최신 yt_dlp 패키지가 들어간다
      yt_dlp/
      yt_dlp-<버전>.dist-info/

pip이 없는 환경(묶인 앱)에서도 되어야 하므로 wheel을 직접 받아 푼다.
yt-dlp wheel은 순수 파이썬이라 이렇게 해도 문제가 없다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import config

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
TIMEOUT = 30


class UpdateError(RuntimeError):
    pass


def runtime_dir() -> Path:
    return config.user_data_dir() / "runtime"


def bootstrap() -> None:
    """받아둔 갱신본이 있으면 그쪽을 먼저 보게 한다.

    yt_dlp를 import 하기 전에 불러야 한다. 진입점 맨 위에서 부르는 이유다.
    """
    directory = runtime_dir()
    if not (directory / "yt_dlp").is_dir():
        return
    path = str(directory)
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


def installed_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # 갱신본이 깨졌을 수도 있다. 버전을 못 읽는다고 앱이 죽으면 안 된다.
        return ""


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _wheel_of(release: dict) -> dict:
    """PyPI 응답에서 순수 파이썬 wheel 하나를 고른다."""
    for item in release.get("urls", []):
        if item.get("packagetype") == "bdist_wheel" and item.get("filename", "").endswith(
            "py3-none-any.whl"
        ):
            return item
    raise UpdateError("받을 수 있는 yt-dlp 파일을 찾지 못했습니다")


def check() -> dict:
    """설치된 버전과 최신 버전을 비교한다. 네트워크가 안 되면 그대로 알린다."""
    current = installed_version()
    try:
        latest = str(_fetch_json(PYPI_URL).get("info", {}).get("version") or "")
    except Exception as exc:
        return {"current": current, "latest": "", "outdated": False, "error": f"확인 실패: {exc}"}
    return {
        "current": current,
        "latest": latest,
        # 버전이 날짜 형식(2026.08.19)이라 문자열 비교로 충분하다
        "outdated": bool(latest and current and latest > current),
        "error": "",
    }


def _download(url: str, sha256: str, dest: Path) -> None:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response, dest.open("wb") as out:
        while chunk := response.read(256 * 1024):
            digest.update(chunk)
            out.write(chunk)
    if sha256 and digest.hexdigest() != sha256:
        raise UpdateError("받은 파일이 손상됐습니다(해시 불일치)")


def update() -> dict:
    """최신 yt-dlp를 받아 사용자 폴더에 푼다. 적용은 앱을 다시 켤 때부터."""
    try:
        release = _fetch_json(PYPI_URL)
    except Exception as exc:
        raise UpdateError(f"최신 버전을 확인하지 못했습니다: {exc}") from exc

    wheel = _wheel_of(release)
    version = str(release.get("info", {}).get("version") or "")
    target = runtime_dir()
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target) as workspace:
        work = Path(workspace)
        archive = work / wheel["filename"]
        try:
            _download(wheel["url"], (wheel.get("digests") or {}).get("sha256", ""), archive)
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(f"내려받지 못했습니다: {exc}") from exc

        staging = work / "unpacked"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
        if not (staging / "yt_dlp").is_dir():
            raise UpdateError("받은 파일에 yt_dlp가 없습니다")

        # 새 것을 다 푼 뒤에 갈아끼운다. 중간에 실패해도 쓰던 것이 남아 있게.
        for item in staging.iterdir():
            final = target / item.name
            if final.exists():
                shutil.rmtree(final, ignore_errors=True) if final.is_dir() else final.unlink()
            shutil.move(str(item), final)

    return {"version": version, "restart_required": True}
