"""빌드 시점의 커밋과 날짜를 app/_build.py 에 새긴다.

데스크톱 앱은 설치해두면 소스를 고쳐도 그대로다. 화면에 어느 빌드인지 없으면
"고쳤는데 왜 그대로지?"가 새 빌드를 안 깐 탓인지 알 수 없다.

    python packaging/stamp.py                  # app/_build.py 를 만든다
    python packaging/stamp.py --print-version  # 버전만 출력 (설치 프로그램용)
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.version import __version__  # noqa: E402


def _git(*args: str) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=10)
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> int:
    if "--print-version" in sys.argv:
        print(__version__)
        return 0

    commit = _git("rev-parse", "--short", "HEAD")
    if commit and _git("status", "--porcelain"):
        commit += "+"  # 커밋하지 않은 수정이 섞인 빌드
    built_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    target = ROOT / "app" / "_build.py"
    target.write_text(
        '"""빌드할 때 자동으로 만들어진다. 손으로 고치지 말 것."""\n\n'
        f'COMMIT = {commit!r}\n'
        f'BUILT_AT = {built_at!r}\n',
        encoding="utf-8",
    )
    print(f"버전 {__version__} · {commit or '커밋 불명'} · {built_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
