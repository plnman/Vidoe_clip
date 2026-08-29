"""프로젝트 상태 관리.

핵심 아이디어: 소스는 '여유분(pad)을 붙여' 한 번만 받아둔다.
그 뒤 구간을 고치고 다시 렌더해도 이미 받아둔 파일만 쓰므로 재다운로드가 없다.
사용자가 여유분 밖으로 끌어야 할 때만 그 구간을 다시 받는다.
"""

from __future__ import annotations

import re
import shutil
import zipfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import config, downloader, media
from .segments import Segment, format_timecode

EPS = 0.05
# 받은 파일 길이는 요청보다 1초 안쪽으로 짧을 수 있다(키프레임/컨테이너 오차).
COVER_TOL = 1.0


class ProjectError(RuntimeError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _safe_filename(name: str, fallback: str = "clip") -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:80]
    return name or fallback


@dataclass
class Clip:
    """받아둔 소스 조각. offset은 소스 영상 기준 이 파일의 시작 시각."""

    id: str
    path: Path
    offset: float
    length: float
    poster: Path | None = None

    @property
    def end(self) -> float:
        return self.offset + self.length

    def covers(self, start: float, end: float) -> bool:
        return self.offset <= start + COVER_TOL and self.end >= end - COVER_TOL

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "offset": round(self.offset, 3),
            "length": round(self.length, 3),
            "has_poster": self.poster is not None,
        }


@dataclass
class Cut:
    """사용자가 조정하는 구간. 시각은 모두 소스 영상 기준."""

    id: str
    start: float
    end: float
    title: str = ""
    enabled: bool = True
    clip_id: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self, clip: Clip | None) -> dict:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "title": self.title,
            "enabled": self.enabled,
            "label": f"{format_timecode(self.start)} – {format_timecode(self.end)}",
            "clip_id": clip.id if clip else None,
            "ready": clip is not None,
            # 미리보기에서 이 구간이 파일 안 어디인지
            "clip_offset": round(clip.offset, 3) if clip else None,
            "clip_length": round(clip.length, 3) if clip else None,
            # 재다운로드 없이 늘릴 수 있는 범위
            "room_before": round(max(0.0, self.start - clip.offset), 3) if clip else 0.0,
            "room_after": round(max(0.0, clip.end - self.end), 3) if clip else 0.0,
        }


@dataclass
class Task:
    kind: str = ""
    status: str = "idle"  # idle | running | done | error | cancelled
    progress: float = 0.0
    # 구간만 받을 때 yt-dlp는 ffmpeg에 맡기고, 그쪽은 바이트 진행률을 주지 않는다.
    # 그럴 때 0%에 멈춘 것처럼 보이지 않도록 화면에 따로 알린다.
    indeterminate: bool = False
    message: str = ""
    error: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 4),
            "indeterminate": self.indeterminate,
            "message": self.message,
            "error": self.error,
        }


@dataclass
class Project:
    id: str
    url: str
    info: downloader.VideoInfo
    dir: Path
    # "youtube" = 링크에서 구간만 받아온다 / "file" = 내 컴퓨터의 파일, 처음부터 전부 있다
    source: str = "youtube"
    max_height: int = config.DEFAULT_HEIGHT
    prefer: str = "compat"
    pad: float = config.DEFAULT_PAD
    whole: bool = False
    clips: dict[str, Clip] = field(default_factory=dict)
    cuts: list[Cut] = field(default_factory=list)
    task: Task = field(default_factory=Task)
    result: Path | None = None
    result_name: str = ""
    result_format: str = ""
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    cancel: threading.Event = field(default_factory=threading.Event)

    # --- 조회 -------------------------------------------------------------
    def clip_for(self, cut: Cut) -> Clip | None:
        """이 구간을 담고 있는 파일. 없으면 None(=다시 받아야 함)."""
        clip = self.clips.get(cut.clip_id or "")
        if clip and clip.covers(cut.start, cut.end):
            return clip
        return next((c for c in self.clips.values() if c.covers(cut.start, cut.end)), None)

    def pending_cuts(self) -> list[Cut]:
        return [c for c in self.cuts if c.enabled and self.clip_for(c) is None]

    def enabled_cuts(self) -> list[Cut]:
        return [c for c in self.cuts if c.enabled and c.duration > EPS]

    def to_dict(self) -> dict:
        cuts = []
        for cut in self.cuts:
            clip = self.clip_for(cut)
            cut.clip_id = clip.id if clip else None
            cuts.append(cut.to_dict(clip))
        enabled = self.enabled_cuts()
        return {
            "id": self.id,
            "source": self.source,
            "video": self.info.to_dict(),
            "options": {
                "max_height": self.max_height,
                "prefer": self.prefer,
                "pad": self.pad,
                "whole": self.whole,
            },
            "cuts": cuts,
            "task": self.task.to_dict(),
            "total_duration": round(sum(c.duration for c in enabled), 3),
            "pending": len(self.pending_cuts()),
            "result": (
                {
                    "name": self.result_name,
                    "size": self.result.stat().st_size,
                    "format": self.result_format,
                    # zip은 브라우저에서 재생할 수 없다
                    "previewable": self.result.suffix.lower() != ".zip",
                }
                if self.result and self.result.exists()
                else None
            ),
        }


class ProjectStore:
    """메모리 상의 프로젝트 목록 + 작업 실행."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=config.MAX_WORKERS, thread_name_prefix="clip")

    # --- 수명주기 ---------------------------------------------------------
    def create(self, url: str) -> Project:
        self.sweep()
        info = downloader.probe_url(url)
        pid = _new_id("p_")
        directory = config.WORK_DIR / pid
        directory.mkdir(parents=True, exist_ok=True)
        project = Project(id=pid, url=info.url, info=info, dir=directory)
        with self._lock:
            self._projects[pid] = project
        return project

    def create_from_file(
        self, path: Path, *, display_name: str = "", take_ownership: bool = False
    ) -> Project:
        """내 컴퓨터의 영상 파일로 프로젝트를 만든다.

        유튜브 소스와 달리 받을 것이 없다. 파일 전체를 담은 조각 하나를 미리 넣어두면
        그다음 흐름(편집·렌더)은 유튜브와 완전히 같은 길을 탄다.

        take_ownership=True면 파일을 프로젝트 폴더로 옮긴다(업로드 임시본).
        False면 원본 자리를 그대로 가리킨다 — 몇 GB짜리를 복사하지 않기 위해서다.
        원본은 프로젝트 폴더 밖이라 정리(delete)에도 지워지지 않는다.
        """
        self.sweep()
        path = Path(path)
        if not path.is_file():
            raise ProjectError("파일을 찾을 수 없습니다")

        # 폴더를 만들기 전에 먼저 읽어본다. 못 읽는 파일이면 여기서 끝낸다.
        try:
            probed = media.probe(path)
        except media.MediaError as exc:
            raise ProjectError(f"영상 파일로 읽지 못했습니다: {exc}") from exc
        if not probed.has_video and not probed.has_audio:
            raise ProjectError("영상도 소리도 없는 파일입니다")
        if probed.duration <= EPS:
            raise ProjectError("길이를 알 수 없는 파일입니다")
        if probed.duration > config.MAX_SOURCE_SECONDS:
            raise ProjectError(f"너무 긴 영상입니다(최대 {config.MAX_SOURCE_SECONDS // 3600}시간)")

        pid = _new_id("p_")
        directory = config.WORK_DIR / pid
        directory.mkdir(parents=True, exist_ok=True)

        if take_ownership:
            owned = directory / f"source{path.suffix.lower() or '.mp4'}"
            shutil.move(str(path), owned)
            path = owned

        clip = Clip(id=_new_id("m_"), path=path, offset=0.0, length=probed.duration)
        clip.poster = media.make_thumbnail(
            path, directory / "source.jpg", at=min(2.0, probed.duration / 2)
        )

        title = _safe_filename(display_name or path.stem, fallback="내 영상")
        info = downloader.VideoInfo(
            video_id="",
            url="",
            title=title,
            duration=probed.duration,
            # 썸네일은 파일에서 뽑은 것을 쓴다. 링크 소스의 유튜브 썸네일 자리와 같다.
            thumbnail=f"/api/projects/{pid}/clips/{clip.id}/poster",
            uploader=f"내 파일 · {probed.width}x{probed.height}" if probed.has_video else "내 파일 · 소리만",
            is_live=False,
        )
        project = Project(id=pid, url="", info=info, dir=directory, source="file")
        project.clips[clip.id] = clip
        with self._lock:
            self._projects[pid] = project
        return project

    def get(self, project_id: str) -> Project:
        with self._lock:
            project = self._projects.get(project_id)
        if project is None:
            raise ProjectError("프로젝트를 찾을 수 없습니다(만료됐을 수 있습니다)")
        project.touched_at = time.time()
        return project

    def delete(self, project_id: str) -> None:
        with self._lock:
            project = self._projects.pop(project_id, None)
        if project:
            project.cancel.set()
            shutil.rmtree(project.dir, ignore_errors=True)

    def clear_all(self) -> dict:
        """열려 있는 프로젝트와 작업 폴더에 남은 찌꺼기를 전부 지운다.

        데스크톱 앱은 작업 파일을 사용자 폴더에 쌓는다(임시 폴더는 OS가 청소해서
        결과물이 사라질 수 있다). 대신 스스로 비울 방법이 있어야 한다.
        """
        with self._lock:
            ids = list(self._projects)
        for project_id in ids:
            self.delete(project_id)

        freed = 0
        if config.WORK_DIR.is_dir():
            for leftover in config.WORK_DIR.iterdir():
                for item in leftover.rglob("*") if leftover.is_dir() else [leftover]:
                    if item.is_file():
                        freed += item.stat().st_size
                shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(
                    missing_ok=True
                )
        return {"projects": len(ids), "freed": freed}

    def sweep(self) -> None:
        """오래된 프로젝트와 파일을 정리한다."""
        cutoff = time.time() - config.PROJECT_TTL_SECONDS
        with self._lock:
            stale = [p for p in self._projects.values() if p.touched_at < cutoff]
        for project in stale:
            self.delete(project.id)

    # --- 편집 -------------------------------------------------------------
    def set_segments(self, project: Project, segments: list[Segment]) -> None:
        with project.lock:
            project.cuts = [
                Cut(id=_new_id("c_"), start=s.start, end=s.end or s.start, title=s.title)
                for s in segments
            ]
            project.result = None

    def update_cuts(self, project: Project, payload: list[dict]) -> None:
        """클라이언트가 보낸 순서/시각/제목으로 통째로 교체한다."""
        duration = project.info.duration
        by_id = {c.id: c for c in project.cuts}
        updated: list[Cut] = []
        for item in payload[: config.MAX_SEGMENTS]:
            cut = by_id.get(str(item.get("id") or ""))
            try:
                start = max(0.0, min(float(item["start"]), duration))
                end = max(0.0, min(float(item["end"]), duration))
            except (KeyError, TypeError, ValueError):
                raise ProjectError("구간 시각이 올바르지 않습니다")
            if end <= start:
                raise ProjectError("끝이 시작보다 빠른 구간이 있습니다")
            if cut is None:
                cut = Cut(id=_new_id("c_"), start=start, end=end)
            cut.start, cut.end = start, end
            cut.title = str(item.get("title") or "")[:200]
            cut.enabled = bool(item.get("enabled", True))
            updated.append(cut)

        total = sum(c.duration for c in updated if c.enabled)
        if total > config.MAX_TOTAL_SECONDS:
            raise ProjectError(f"전체 길이가 너무 깁니다(최대 {config.MAX_TOTAL_SECONDS // 3600}시간)")

        with project.lock:
            project.cuts = updated
            project.result = None

    def set_options(self, project: Project, **options) -> None:
        with project.lock:
            if "max_height" in options:
                project.max_height = max(144, min(int(options["max_height"]), 4320))
            if "prefer" in options and options["prefer"] in ("compat", "small"):
                project.prefer = options["prefer"]
            if "pad" in options:
                project.pad = max(0.0, min(float(options["pad"]), config.MAX_PAD))
            if "whole" in options:
                project.whole = bool(options["whole"])

    # --- 작업 -------------------------------------------------------------
    def _begin(self, project: Project, kind: str, message: str) -> None:
        with project.lock:
            if project.task.status == "running":
                raise ProjectError("이미 작업이 진행 중입니다")
            project.cancel = threading.Event()
            project.task = Task(kind=kind, status="running", progress=0.0, message=message)

    def _finish(self, project: Project, status: str, message: str, error: str = "") -> None:
        with project.lock:
            project.task.status = status
            project.task.message = message
            project.task.error = error
            project.task.progress = 1.0 if status == "done" else project.task.progress
            project.task.updated_at = time.time()

    def cancel(self, project: Project) -> None:
        project.cancel.set()

    def start_prepare(self, project: Project) -> None:
        if not project.enabled_cuts():
            raise ProjectError("구간을 먼저 입력하세요")
        self._begin(project, "prepare", "소스 준비 중")
        self._pool.submit(self._prepare, project)

    def start_render(self, project: Project, fmt: str, quality: str, separate: bool) -> None:
        if not project.enabled_cuts():
            raise ProjectError("구간을 먼저 입력하세요")
        if project.pending_cuts():
            raise ProjectError("아직 받지 않은 구간이 있습니다. 먼저 소스를 준비하세요")
        try:
            media.format_spec(fmt)
        except media.MediaError as exc:
            raise ProjectError(str(exc)) from exc
        self._begin(project, "render", "구간별로 저장하는 중" if separate else "이어붙이는 중")
        self._pool.submit(self._render, project, fmt, quality, separate)

    # --- 실행부 -----------------------------------------------------------
    def _needed_ranges(self, project: Project) -> list[tuple[float, float]]:
        """아직 없는 구간들을 여유분 포함해 묶는다(겹치면 한 번에 받도록)."""
        duration = project.info.duration
        pad = project.pad
        wanted = sorted(
            (max(0.0, c.start - pad), min(duration, c.end + pad)) for c in project.pending_cuts()
        )
        merged: list[list[float]] = []
        for start, end in wanted:
            if merged and start <= merged[-1][1] + 1.0:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(a, b) for a, b in merged if b - a > EPS]

    def _prepare(self, project: Project) -> None:
        try:
            if project.source == "file":
                # 파일 소스는 만들 때 이미 전체를 담은 조각을 넣어뒀다. 받을 것이 없다.
                self._finish(project, "done", "편집할 준비가 됐습니다")
                return

            if project.whole:
                have_full = any(
                    c.offset <= COVER_TOL and c.end >= project.info.duration - COVER_TOL
                    for c in project.clips.values()
                )
                ranges: list[tuple[float, float] | None] = [] if have_full else [None]
            else:
                ranges = list(self._needed_ranges(project))

            if not ranges:
                self._finish(project, "done", "이미 모두 준비돼 있습니다")
                return

            weights = [(1.0 if r is None else r[1] - r[0]) for r in ranges]
            total_weight = sum(weights) or 1.0
            done_weight = 0.0

            for index, rng in enumerate(ranges):
                if project.cancel.is_set():
                    raise downloader.Cancelled("취소되었습니다")
                start, end = (None, None) if rng is None else rng
                label = (
                    "전체 영상 받는 중"
                    if rng is None
                    else f"{format_timecode(start)}~{format_timecode(end)} 받는 중 "
                    f"({index + 1}/{len(ranges)})"
                )

                def on_progress(fraction: float, base=done_weight, weight=weights[index]) -> None:
                    project.task.indeterminate = False
                    project.task.progress = min(0.99, (base + fraction * weight) / total_weight)

                project.task.message = label
                # 진행률이 올지 안 올지는 받아 봐야 안다. 일단 모른다고 표시해 둔다.
                project.task.indeterminate = True
                path = downloader.fetch_range(
                    project.url,
                    project.dir,
                    _new_id("f_"),
                    start=start,
                    end=end,
                    max_height=project.max_height,
                    prefer=project.prefer,
                    on_progress=on_progress,
                    cancel=project.cancel,
                )
                probed = media.probe(path)
                clip = Clip(
                    id=_new_id("m_"),
                    path=path,
                    offset=0.0 if rng is None else float(start),
                    length=probed.duration if rng is not None else max(probed.duration, project.info.duration),
                )
                poster = media.make_thumbnail(path, path.with_suffix(".jpg"), at=min(1.0, probed.duration / 2))
                clip.poster = poster
                with project.lock:
                    project.clips[clip.id] = clip
                done_weight += weights[index]
                project.task.indeterminate = False
                project.task.progress = min(0.99, done_weight / total_weight)

            remaining = len(project.pending_cuts())
            if remaining:
                self._finish(
                    project, "error", "준비 실패",
                    f"{remaining}개 구간을 받지 못했습니다. 화질을 낮추거나 다시 시도해 보세요.",
                )
            else:
                self._finish(project, "done", "편집할 준비가 됐습니다")
        except downloader.Cancelled:
            self._finish(project, "cancelled", "취소했습니다")
        except (downloader.DownloadFailed, media.MediaError, ValueError) as exc:
            self._finish(project, "error", "준비 실패", str(exc))
        except Exception as exc:  # 예기치 못한 오류도 UI에 남긴다
            self._finish(project, "error", "준비 실패", f"알 수 없는 오류: {exc}")

    def _media_cut(self, project: Project, cut: Cut) -> media.Cut:
        clip = project.clip_for(cut)
        if clip is None:
            raise ProjectError("준비되지 않은 구간이 있습니다")
        return media.Cut(
            path=clip.path,
            start=max(0.0, cut.start - clip.offset),
            end=min(clip.length, cut.end - clip.offset),
        )

    def _render(self, project: Project, fmt: str, quality: str, separate: bool) -> None:
        try:
            spec = media.format_spec(fmt)
            cuts = project.enabled_cuts()
            title = _safe_filename(project.info.title)

            if separate:
                out_path = self._render_each(project, cuts, fmt, quality, spec["ext"])
                name = f"{title}_구간{len(cuts)}개.zip"
            else:
                out_path = project.dir / f"result{spec['ext']}"
                media.render(
                    [self._media_cut(project, cut) for cut in cuts],
                    out_path,
                    fmt=fmt,
                    quality=quality,
                    on_progress=lambda f: setattr(project.task, "progress", min(0.99, f)),
                    cancel=project.cancel,
                )
                name = f"{title}_편집본{spec['ext']}"

            with project.lock:
                project.result = out_path
                project.result_name = name
                project.result_format = fmt
            self._finish(project, "done", "완성했습니다")
        except media.Cancelled:
            self._finish(project, "cancelled", "취소했습니다")
        except (media.MediaError, ProjectError) as exc:
            self._finish(project, "error", "렌더 실패", str(exc))
        except Exception as exc:
            self._finish(project, "error", "렌더 실패", f"알 수 없는 오류: {exc}")

    def _render_each(self, project, cuts: list[Cut], fmt: str, quality: str, ext: str) -> Path:
        """구간마다 파일 하나씩 만들어 zip으로 묶는다."""
        parts_dir = project.dir / "parts"
        shutil.rmtree(parts_dir, ignore_errors=True)
        parts_dir.mkdir(parents=True, exist_ok=True)

        total = sum(cut.duration for cut in cuts) or 1.0
        done = 0.0
        made: list[Path] = []

        for index, cut in enumerate(cuts, 1):
            if project.cancel.is_set():
                raise media.Cancelled("취소되었습니다")
            label = _safe_filename(cut.title, fallback=format_timecode(cut.start).replace(":", "-"))
            part = parts_dir / f"{index:02d}_{label}{ext}"
            project.task.message = f"{index}/{len(cuts)}번째 구간 저장 중"

            def on_progress(fraction: float, base=done, weight=cut.duration) -> None:
                project.task.progress = min(0.98, (base + fraction * weight) / total)

            media.render(
                [self._media_cut(project, cut)],
                part,
                fmt=fmt,
                quality=quality,
                on_progress=on_progress,
                cancel=project.cancel,
            )
            made.append(part)
            done += cut.duration

        project.task.message = "압축하는 중"
        archive = project.dir / "result.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as bundle:
            for part in made:
                bundle.write(part, arcname=part.name)
        return archive


store = ProjectStore()
