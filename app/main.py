"""유튜브 구간 편집기 웹앱.

흐름: 링크 확인 → 구간 붙여넣기 → 소스 준비(다운로드) → 구간 보며 편집 → 렌더 → 다운로드.
편집 후 재렌더는 이미 받아둔 파일만 쓰므로 다시 다운로드하지 않는다.
"""

from __future__ import annotations

import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, downloader, media, projects, segments as seg, updater
from .projects import ProjectError, store

STATIC_DIR = Path(__file__).parent / "static"
COOKIE_NAME = "clipper_auth"


def _lan_ip() -> str:
    """이 컴퓨터가 공유기에서 쓰는 주소. 다른 기기에서 접속할 때 필요하다."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("8.8.8.8", 80))  # 실제로 패킷을 보내지는 않는다
            return probe.getsockname()[0]
    except OSError:
        return ""


def announce() -> None:
    """실행하자마자 어디로 접속하면 되는지 알려준다."""
    lines = ["", f"  이 컴퓨터에서:  http://127.0.0.1:{config.PORT}"]
    if config.HOST in ("0.0.0.0", "::"):
        address = _lan_ip()
        if address:
            lines.append(f"  같은 공유기의 다른 기기에서:  http://{address}:{config.PORT}")
        if not config.PASSWORD:
            lines.append("  ! 네트워크에 열려 있는데 비밀번호가 없습니다 — CLIPPER_PASSWORD를 설정하세요")
    try:
        media.ensure_tools()
    except media.MediaError as exc:
        lines.append(f"  ! {exc}")
    print("\n".join(lines + [""]), flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    announce()
    yield


app = FastAPI(title="유튜브 구간 편집기", docs_url=None, redoc_url=None, lifespan=lifespan)


# --- 인증 (비밀번호를 설정한 경우에만) -------------------------------------
def _token() -> str:
    return secrets.token_hex(16) if not config.PASSWORD else config.PASSWORD


def require_auth(request: Request) -> None:
    if not config.PASSWORD:
        return
    supplied = request.headers.get("x-clipper-password") or request.cookies.get(COOKIE_NAME) or ""
    if not secrets.compare_digest(supplied, config.PASSWORD):
        raise HTTPException(status_code=401, detail="비밀번호가 필요합니다")


class LoginBody(BaseModel):
    password: str = ""


@app.post("/api/login")
def login(body: LoginBody, response: Response) -> dict:
    if config.PASSWORD and not secrets.compare_digest(body.password, config.PASSWORD):
        raise HTTPException(status_code=401, detail="비밀번호가 틀렸습니다")
    response.set_cookie(COOKIE_NAME, config.PASSWORD, httponly=True, samesite="lax", max_age=30 * 86400)
    return {"ok": True}


def _is_loopback(request: Request) -> bool:
    """서버를 돌리는 그 PC에서 열었는지. 파일 경로를 직접 받아도 되는 조건이다."""
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1")


def require_loopback(request: Request) -> None:
    if not _is_loopback(request):
        raise HTTPException(
            status_code=403,
            detail="파일 경로는 앱을 켜 둔 그 PC에서만 쓸 수 있습니다. 다른 기기에서는 파일을 올려주세요.",
        )


@app.get("/api/health")
def health(request: Request) -> dict:
    try:
        media.ensure_tools()
        ffmpeg_ok, ffmpeg_error = True, ""
    except media.MediaError as exc:
        ffmpeg_ok, ffmpeg_error = False, str(exc)
    return {
        "ok": ffmpeg_ok,
        "ffmpeg": ffmpeg_ok,
        "error": ffmpeg_error,
        "auth_required": bool(config.PASSWORD),
        # 이 PC에서 연 화면이면 파일을 올리지 않고 경로만으로 바로 쓸 수 있다
        "local_files": _is_loopback(request),
        "file_picker": _is_loopback(request) and _picker_available(),
        "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
        "work_dir": str(config.WORK_DIR),
        "defaults": {
            "pad": config.DEFAULT_PAD,
            "max_pad": config.MAX_PAD,
            "height": config.DEFAULT_HEIGHT,
            "max_segments": config.MAX_SEGMENTS,
        },
        "presets": {k: v["label"] for k, v in config.RENDER_PRESETS.items()},
        "formats": {k: v["label"] for k, v in media.FORMATS.items()},
        "default_format": media.DEFAULT_FORMAT,
    }


# --- 요청 본문 -------------------------------------------------------------
class UrlBody(BaseModel):
    url: str


class ParseBody(BaseModel):
    text: str = ""
    duration: float | None = None
    pad: float | None = None


class SegmentsBody(BaseModel):
    text: str = ""
    merge_overlaps: bool = False


class CutBody(BaseModel):
    id: str = ""
    start: float
    end: float
    title: str = ""
    enabled: bool = True


class CutsBody(BaseModel):
    cuts: list[CutBody] = Field(default_factory=list)


class OptionsBody(BaseModel):
    max_height: int | None = None
    prefer: str | None = None
    pad: float | None = None
    whole: bool | None = None


class RenderBody(BaseModel):
    format: str = media.DEFAULT_FORMAT
    quality: str = "fast"
    # 이어붙이지 않고 구간마다 파일 하나씩 만들어 zip으로 준다
    separate: bool = False


# --- API -------------------------------------------------------------------
@app.get("/api/updates", dependencies=[Depends(require_auth)])
def check_updates() -> dict:
    """yt-dlp가 낡았는지 본다. 유튜브가 바뀌면 이것부터 갱신해야 한다."""
    return updater.check()


@app.post("/api/updates/ytdlp", dependencies=[Depends(require_auth), Depends(require_loopback)])
def update_ytdlp() -> dict:
    try:
        return updater.update()
    except updater.UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/work/clear", dependencies=[Depends(require_auth), Depends(require_loopback)])
def clear_work() -> dict:
    return {**store.clear_all(), "dir": str(config.WORK_DIR)}


@app.post("/api/parse", dependencies=[Depends(require_auth)])
def parse(body: ParseBody) -> dict:
    result = seg.parse_segments(body.text, body.duration)
    parsed = result.to_dict()
    # 실제로 받아야 할 길이. 여유분이 붙고 붙어 있는 구간끼리 묶이므로 구간 합계보다 크다.
    # 이게 영상 길이에 가까워지면 통째로 받는 편이 빠르다고 화면이 알려준다.
    if body.duration:
        needs = projects.merge_needs(
            ((s.start, s.end or s.start, i) for i, s in enumerate(result.segments, 1)),
            body.pad if body.pad is not None else config.DEFAULT_PAD,
            body.duration,
        )
        parsed["source_span"] = round(sum(need.length for need in needs), 1)
    return parsed


@app.post("/api/projects", dependencies=[Depends(require_auth)])
def create_project(body: UrlBody) -> dict:
    try:
        return store.create(body.url).to_dict()
    except (ValueError, downloader.DownloadFailed) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class LocalPathBody(BaseModel):
    path: str = ""


def _checked_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in config.VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail="영상 또는 소리 파일이 아닙니다")
    return suffix


@app.post("/api/projects/local", dependencies=[Depends(require_auth), Depends(require_loopback)])
def create_project_from_path(body: LocalPathBody) -> dict:
    """이 PC의 파일을 그 자리에서 쓴다. 복사하지 않으므로 용량과 무관하게 즉시 열린다."""
    raw = (body.path or "").strip().strip('"')
    if not raw:
        raise HTTPException(status_code=400, detail="파일 경로를 입력하세요")
    path = Path(raw).expanduser()
    _checked_suffix(path.name)
    try:
        return store.create_from_file(path).to_dict()
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/upload", dependencies=[Depends(require_auth)])
async def create_project_from_upload(file: UploadFile = File(...)) -> dict:
    """다른 기기에서 접속했을 때 쓰는 길. 올려받아 프로젝트 폴더에 둔다."""
    name = Path(file.filename or "").name
    suffix = _checked_suffix(name)
    staging = config.WORK_DIR / "_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    temp = staging / f"{secrets.token_hex(8)}{suffix}"
    try:
        size = 0
        with temp.open("wb") as out:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"파일이 너무 큽니다(최대 {config.MAX_UPLOAD_BYTES // (1024 * 1024)}MB). "
                        "앱을 켜 둔 PC의 파일이라면 경로로 여는 쪽이 빠릅니다.",
                    )
                out.write(chunk)
        return store.create_from_file(
            temp, display_name=Path(name).stem, take_ownership=True
        ).to_dict()
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        # 프로젝트가 만들어졌으면 이미 옮겨졌다. 실패했을 때만 남아 있다.
        temp.unlink(missing_ok=True)


def _picker_available() -> bool:
    from . import desktop

    return desktop.picker_available()


@app.post("/api/pick-file", dependencies=[Depends(require_auth), Depends(require_loopback)])
def pick_file() -> dict:
    """데스크톱 앱 창에서 OS 파일 선택 대화상자를 연다."""
    from . import desktop

    if not desktop.picker_available():
        raise HTTPException(status_code=409, detail="앱 창에서만 쓸 수 있습니다")
    return {"path": desktop.pick_video_file()}


@app.get("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
def get_project(project_id: str) -> dict:
    return store.get(project_id).to_dict()


@app.delete("/api/projects/{project_id}", dependencies=[Depends(require_auth)])
def delete_project(project_id: str) -> dict:
    store.delete(project_id)
    return {"ok": True}


@app.post("/api/projects/{project_id}/segments", dependencies=[Depends(require_auth)])
def set_segments(project_id: str, body: SegmentsBody) -> dict:
    project = store.get(project_id)
    result = seg.parse_segments(body.text, project.info.duration)
    parsed = seg.merge_overlaps(result.segments) if body.merge_overlaps else result.segments
    if not parsed:
        raise HTTPException(status_code=400, detail="읽어낸 구간이 없습니다")
    store.set_segments(project, parsed)
    return {"project": project.to_dict(), "parse": result.to_dict()}


@app.patch("/api/projects/{project_id}/cuts", dependencies=[Depends(require_auth)])
def update_cuts(project_id: str, body: CutsBody) -> dict:
    project = store.get(project_id)
    try:
        store.update_cuts(project, [c.model_dump() for c in body.cuts])
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return project.to_dict()


@app.patch("/api/projects/{project_id}/options", dependencies=[Depends(require_auth)])
def update_options(project_id: str, body: OptionsBody) -> dict:
    project = store.get(project_id)
    store.set_options(project, **{k: v for k, v in body.model_dump().items() if v is not None})
    return project.to_dict()


@app.post("/api/projects/{project_id}/prepare", dependencies=[Depends(require_auth)])
def prepare(project_id: str) -> dict:
    project = store.get(project_id)
    try:
        store.start_prepare(project)
    except ProjectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return project.to_dict()


@app.post("/api/projects/{project_id}/render", dependencies=[Depends(require_auth)])
def render(project_id: str, body: RenderBody) -> dict:
    project = store.get(project_id)
    try:
        store.start_render(project, body.format, body.quality, body.separate)
    except ProjectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return project.to_dict()


@app.post("/api/projects/{project_id}/cancel", dependencies=[Depends(require_auth)])
def cancel(project_id: str) -> dict:
    project = store.get(project_id)
    store.cancel(project)
    return project.to_dict()


# --- 미디어 전송 (Range 지원 → 브라우저에서 탐색 가능) ----------------------
@app.get("/api/projects/{project_id}/clips/{clip_id}/media", dependencies=[Depends(require_auth)])
def clip_media(project_id: str, clip_id: str) -> FileResponse:
    project = store.get(project_id)
    clip = project.clips.get(clip_id)
    if clip is None or not clip.path.exists():
        raise HTTPException(status_code=404, detail="조각을 찾을 수 없습니다")
    return FileResponse(clip.path, media_type="video/mp4")


@app.get("/api/projects/{project_id}/clips/{clip_id}/poster", dependencies=[Depends(require_auth)])
def clip_poster(project_id: str, clip_id: str) -> FileResponse:
    project = store.get(project_id)
    clip = project.clips.get(clip_id)
    if clip is None or clip.poster is None or not clip.poster.exists():
        raise HTTPException(status_code=404, detail="썸네일이 없습니다")
    return FileResponse(clip.poster, media_type="image/jpeg")


_MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".gif": "image/gif",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".zip": "application/zip",
}


def _mime_of(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


@app.get("/api/projects/{project_id}/result", dependencies=[Depends(require_auth)])
def result_media(project_id: str) -> FileResponse:
    project = store.get(project_id)
    if not project.result or not project.result.exists():
        raise HTTPException(status_code=404, detail="아직 결과물이 없습니다")
    return FileResponse(project.result, media_type=_mime_of(project.result))


@app.get("/api/projects/{project_id}/download", dependencies=[Depends(require_auth)])
def download(project_id: str) -> FileResponse:
    project = store.get(project_id)
    if not project.result or not project.result.exists():
        raise HTTPException(status_code=404, detail="아직 결과물이 없습니다")
    return FileResponse(project.result, filename=project.result_name)


@app.exception_handler(ProjectError)
def project_error_handler(request: Request, exc: ProjectError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
