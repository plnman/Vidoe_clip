"""앱 버전. 어느 빌드를 돌리고 있는지 알 수 있어야 한다.

데스크톱 앱은 설치해두면 소스를 고쳐도 그대로다. 화면에 버전이 없으면
"고쳤는데 왜 그대로지?"의 원인이 새 빌드를 안 깐 것인지 아닌지 알 수 없다.

빌드할 때 packaging/build.* 가 app/_build.py 를 만들어 커밋 해시와 날짜를 남긴다.
소스에서 바로 돌릴 때는 git에서 읽는다.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

__version__ = "1.2.1"

_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def build_stamp() -> dict:
    """커밋 해시와 빌드 시각. 알 수 없으면 빈 값."""
    try:
        from ._build import BUILT_AT, COMMIT  # 빌드할 때 만들어진다

        return {"commit": COMMIT, "built_at": BUILT_AT}
    except ImportError:
        pass

    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=5,
        )
        commit = done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        commit = ""
    return {"commit": commit, "built_at": ""}


def display() -> str:
    """화면에 보여줄 한 줄. 예: 1.1.0 (85ce1dc · 2026-09-24)"""
    stamp = build_stamp()
    extra = " · ".join(part for part in (stamp["commit"], stamp["built_at"]) if part)
    return f"{__version__} ({extra})" if extra else __version__


def info() -> dict:
    return {"version": __version__, "display": display(), **build_stamp()}
