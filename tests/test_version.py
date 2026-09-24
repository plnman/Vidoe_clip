"""앱 버전 표시. 어느 빌드를 돌리는지 알 수 있어야 한다."""

import subprocess
import sys
from pathlib import Path

from app import version

ROOT = Path(__file__).resolve().parent.parent


def test_display_always_contains_the_version_number():
    assert version.__version__ in version.display()


def test_info_has_the_fields_the_screen_uses():
    info = version.info()
    assert set(info) == {"version", "display", "commit", "built_at"}
    assert info["display"] == version.display()


def test_build_stamp_prefers_the_generated_file(monkeypatch, tmp_path):
    """빌드할 때 새긴 값이 있으면 git보다 그것을 쓴다."""
    module = type(sys)("app._build")
    module.COMMIT = "abc1234"
    module.BUILT_AT = "2026-09-24 13:22"
    monkeypatch.setitem(sys.modules, "app._build", module)
    version.build_stamp.cache_clear()
    try:
        stamp = version.build_stamp()
        assert stamp == {"commit": "abc1234", "built_at": "2026-09-24 13:22"}
        assert "abc1234" in version.display()
        assert "2026-09-24" in version.display()
    finally:
        version.build_stamp.cache_clear()


def test_stamp_script_writes_commit_and_date(tmp_path, monkeypatch):
    target = ROOT / "app" / "_build.py"
    before = target.read_text(encoding="utf-8") if target.exists() else None
    try:
        done = subprocess.run([sys.executable, str(ROOT / "packaging" / "stamp.py")],
                              capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        assert done.returncode == 0, done.stderr
        written = target.read_text(encoding="utf-8")
        assert "COMMIT" in written and "BUILT_AT" in written
        assert version.__version__ in done.stdout
    finally:
        if before is not None:
            target.write_text(before, encoding="utf-8")
        elif target.exists():
            target.unlink()


def test_stamp_script_can_print_the_version_alone():
    """설치 프로그램이 같은 버전을 쓰도록 이 출력을 읽는다."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "stamp.py"), "--print-version"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    assert done.returncode == 0
    assert done.stdout.strip() == version.__version__


def test_installer_takes_the_version_from_outside():
    """installer.iss에 버전을 박아두면 빌드와 어긋난다."""
    text = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "#ifndef AppVersion" in text
    assert "AppVersion={#AppVersion}" in text
