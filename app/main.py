"""유튜브 구간 편집기 웹앱.

흐름: 링크 확인 → 구간 붙여넣기 → 소스 준비(다운로드) → 구간 보며 편집 → 렌더 → 다운로드.
편집 후 재렌더는 이미 받아둔 파일만 쓰므로 다시 다운로드하지 않는다.
"""

from __future__ import annotations

import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, downloader, media, segments as seg
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


@app.get("/api/health")
def health() -> dict:
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
@app.post("/api/parse", dependencies=[Depends(require_auth)])
def parse(body: ParseBody) -> dict:
    return seg.parse_segments(body.text, body.duration).to_dict()


@app.post("/api/projects", dependencies=[Depends(require_auth)])
def create_project(body: UrlBody) -> dict:
    try:
        return store.create(body.url).to_dict()
    except (ValueError, downloader.DownloadFailed) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
