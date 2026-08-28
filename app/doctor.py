"""실제 유튜브 영상으로 어디까지 되는지 단계별로 찍어 주는 진단 도구.

    python -m app.doctor "https://youtu.be/..."

각 단계를 실제로 실행하고, 실패하면 어디서 왜 막혔는지와 대처를 알려준다.
결과를 그대로 복사해 붙이면 원인을 짚을 수 있다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, downloader, media
from .segments import format_timecode

PROBE_SECONDS = 5.0


@dataclass
class Check:
    name: str
    ok: bool = False
    detail: str = ""
    hint: str = ""
    skipped: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if not c.skipped)

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check


def _tool_version(name: str) -> str:
    path = shutil.which(name)
    if not path:
        return ""
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=15).stdout
        return out.splitlines()[0][:90] if out else path
    except (OSError, subprocess.SubprocessError):
        return path


def _hint_for(message: str) -> str:
    lowered = message.lower()
    if "봇으로 판단" in message or "sign in to confirm" in lowered:
        return ("유튜브가 이 컴퓨터를 봇으로 봤습니다. 브라우저에서 쿠키를 뽑아 넘기세요:\n"
                "     CLIPPER_COOKIES=/경로/cookies.txt python -m app.doctor <URL>\n"
                "     또는 같은 PC의 브라우저에서 직접: CLIPPER_COOKIES_FROM_BROWSER=chrome")
    if "지역" in message or "unavailable" in lowered:
        return "다른 영상으로 다시 해보세요. 지역 제한이나 삭제된 영상일 수 있습니다."
    if "비공개" in message or "멤버십" in message:
        return "공개 영상으로 다시 해보세요."
    if "requested format" in lowered:
        return "화질을 낮춰 보세요: --height 720"
    if "network" in lowered or "timed out" in lowered or "timeout" in lowered:
        return "네트워크나 방화벽 문제일 수 있습니다. 회사망이라면 다른 망에서 시도해 보세요."
    return ""


def run_checks(url: str, height: int = 720, workdir: Path | None = None) -> Report:
    report = Report()
    temp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="clipper-doctor-"))
    temp.mkdir(parents=True, exist_ok=True)

    # 1. ffmpeg
    version = _tool_version("ffmpeg")
    report.add(Check(
        "ffmpeg 설치", bool(version), version or "찾지 못했습니다",
        "" if version else "brew install ffmpeg / sudo apt install ffmpeg / winget install Gyan.FFmpeg",
    ))

    # 2. yt-dlp
    import yt_dlp
    report.add(Check("yt-dlp 버전", True, yt_dlp.version.__version__,
                     "1년 이상 지난 버전이면 pip install -U yt-dlp 하세요"))

    # 3. 링크 해석
    link = report.add(Check("링크 해석"))
    try:
        normalized = downloader.normalize_url(url)
        link.ok, link.detail = True, normalized
    except ValueError as exc:
        link.detail, link.hint = str(exc), "개별 영상 링크인지 확인하세요(재생목록·채널 링크는 안 됩니다)."
        _skip_rest(report, ["영상 정보 조회", "구간 다운로드", "받은 파일 확인", "잘라 이어붙이기"])
        return report

    # 4. 정보 조회 (다운로드 없음)
    info_check = report.add(Check("영상 정보 조회"))
    try:
        info = downloader.probe_url(normalized)
        info_check.ok = True
        info_check.detail = f"{info.title} · {format_timecode(info.duration)}"
    except downloader.DownloadFailed as exc:
        info_check.detail, info_check.hint = str(exc), _hint_for(str(exc))
        _skip_rest(report, ["구간 다운로드", "받은 파일 확인", "잘라 이어붙이기"])
        return report

    # 5. 실제로 5초만 받아 본다
    start = max(0.0, min(info.duration - PROBE_SECONDS - 1, info.duration / 3))
    end = start + PROBE_SECONDS
    fetch = report.add(Check(f"구간 다운로드 ({format_timecode(start)}~{format_timecode(end)}, {height}p)"))
    clip: Path | None = None
    try:
        clip = downloader.fetch_range(normalized, temp, "probe", start=start, end=end, max_height=height)
        fetch.ok = True
        fetch.detail = f"{clip.name} · {clip.stat().st_size / 1024:.0f} KB"
    except (downloader.DownloadFailed, downloader.Cancelled) as exc:
        fetch.detail, fetch.hint = str(exc), _hint_for(str(exc))
        _skip_rest(report, ["받은 파일 확인", "잘라 이어붙이기"])
        return report

    # 6. 받은 파일이 요청한 길이와 맞는지 (여유분 계산의 전제)
    shape = report.add(Check("받은 파일 확인"))
    try:
        probed = media.probe(clip)
        close_enough = abs(probed.duration - PROBE_SECONDS) <= 1.5
        shape.ok = close_enough and probed.has_video
        shape.detail = (f"{probed.width}x{probed.height} {probed.fps:g}fps "
                        f"{probed.duration:.1f}초 (요청 {PROBE_SECONDS:.0f}초)"
                        f"{'' if probed.has_audio else ' · 소리 없음'}")
        if not close_enough:
            shape.hint = "받은 길이가 요청과 다릅니다. 이 내용을 그대로 알려주세요."
    except media.MediaError as exc:
        shape.detail = str(exc)

    # 7. 잘라서 이어붙이기까지
    cut = report.add(Check("잘라 이어붙이기"))
    try:
        out = media.render([media.Cut(clip, 0.5, 2.5), media.Cut(clip, 3.0, 4.0)], temp / "out.mp4")
        result = media.probe(out)
        cut.ok = abs(result.duration - 3.0) <= 0.8
        cut.detail = f"{out.stat().st_size / 1024:.0f} KB · {result.duration:.1f}초 (기대 3.0초)"
    except media.MediaError as exc:
        cut.detail = str(exc)

    return report


def _skip_rest(report: Report, names: list[str]) -> None:
    for name in names:
        report.add(Check(name, skipped=True, detail="앞 단계가 실패해 건너뜀"))


def render_report(report: Report) -> str:
    lines = ["", "유튜브 구간 편집기 — 진단 결과", "=" * 44]
    for check in report.checks:
        mark = "…" if check.skipped else ("OK" if check.ok else "실패")
        lines.append(f"[{mark:^4}] {check.name}")
        if check.detail:
            lines.append(f"        {check.detail}")
        if check.hint and not check.ok:
            lines.append(f"   대처: {check.hint}")
    lines.append("=" * 44)
    lines.append("모든 단계 통과 — 앱을 그대로 쓰시면 됩니다." if report.ok
                 else "위 '실패' 줄과 그 아래 내용을 그대로 복사해 알려주세요.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    height = 720
    if "--height" in args:
        index = args.index("--height")
        height = int(args[index + 1])
        del args[index:index + 2]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("  --height 720   시험 삼아 받을 화질 (기본 720)")
        if config.COOKIE_FILE or config.COOKIES_FROM_BROWSER:
            print("\n  쿠키 설정이 켜져 있습니다.")
        return 0 if args else 1

    report = run_checks(args[0], height=height)
    print(render_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
