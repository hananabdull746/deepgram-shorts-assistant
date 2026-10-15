from __future__ import annotations

import json
import math
import mimetypes
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import Database
from .security import check_csrf, csrf_token, require_admin, verify_admin
from .worker import Worker

settings.ensure_dirs()
DB = Database(settings.database_path)
WORKER = Worker(DB)
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _format_duration(value):
    if value is None:
        return "—"
    seconds = max(0, int(float(value)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _format_timecode(value):
    return _format_duration(value)


def _format_timestamp(value):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


templates.env.filters["duration"] = _format_duration
templates.env.filters["timecode"] = _format_timecode
templates.env.filters["timestamp"] = _format_timestamp


@asynccontextmanager
async def lifespan(app: FastAPI):
    WORKER.start()
    try:
        yield
    finally:
        WORKER.stop()


app = FastAPI(title="ClipCue", version="1.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or "development-only-change-this",
                   session_cookie="clipcue_session", same_site="lax", https_only=settings.cookie_secure,
                   max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def page(request: Request, name: str, **context):
    context.setdefault("csrf", csrf_token(request))
    context.setdefault("admin", request.session.get("admin"))
    context.setdefault("deepgram_configured", bool(settings.deepgram_api_key))
    return templates.TemplateResponse(request=request, name=name, context=context)


def _project_or_404(project_id: str) -> dict:
    project = DB.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _transcript_text(payload: dict) -> str:
    try:
        utterances = payload["results"].get("utterances") or []
        if utterances:
            return " ".join(str(item.get("transcript", "")) for item in utterances).strip()
        return str(payload["results"]["channels"][0]["alternatives"][0].get("transcript", ""))
    except (KeyError, IndexError, TypeError):
        return ""


def _excerpt(payload: dict, start: float, end: float) -> str:
    try:
        utterances = payload["results"].get("utterances") or []
        return " ".join(str(item.get("transcript", "")) for item in utterances
                         if float(item.get("start", 0)) < end and float(item.get("end", 0)) > start).strip()
    except (KeyError, TypeError, ValueError):
        return ""


@app.get("/health")
def health():
    return {"ok": True, "deepgram_configured": bool(settings.deepgram_api_key)}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("admin"):
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", error=request.query_params.get("error", ""))


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form(...)):
    check_csrf(request, csrf)
    if not verify_admin(username, password, settings.admin_username, settings.admin_password):
        return RedirectResponse("/login?error=Invalid%20username%20or%20password", status_code=303)
    request.session.clear()
    request.session["admin"] = settings.admin_username
    csrf_token(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(...), _: str = Depends(require_admin)):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _: str = Depends(require_admin)):
    return page(request, "dashboard.html", projects=DB.list_projects(), message=request.query_params.get("message", ""),
                error=request.query_params.get("error", ""), max_upload_mb=settings.max_upload_bytes // (1024 * 1024))


@app.post("/projects/upload")
async def upload_video(request: Request, title: str = Form(""), video: UploadFile = File(...),
                       csrf: str = Form(...), _: str = Depends(require_admin)):
    check_csrf(request, csrf)
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        await video.close()
        return RedirectResponse("/?error=Choose%20an%20MP4%2C%20MOV%2C%20MKV%2C%20WebM%2C%20or%20M4V%20video", status_code=303)
    project_id = uuid.uuid4().hex
    safe_filename = f"{project_id}{suffix}"
    destination = settings.uploads_dir / safe_filename
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ValueError("File exceeds the configured upload size limit")
                output.write(chunk)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        await video.close()
        return RedirectResponse("/?error=" + quote(str(exc)[:250]), status_code=303)
    finally:
        await video.close()
    if total == 0:
        destination.unlink(missing_ok=True)
        return RedirectResponse("/?error=The%20uploaded%20file%20was%20empty", status_code=303)
    name = Path(video.filename or "video").name[:180]
    project_title = (title.strip() or Path(name).stem or "Untitled video")[:120]
    DB.create_project({"id": project_id, "title": project_title, "original_name": name,
                       "source_path": str(destination)})
    return RedirectResponse(f"/projects/{project_id}?message=Video%20queued%20for%20transcription", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: str, _: str = Depends(require_admin)):
    project = _project_or_404(project_id)
    clips = DB.list_clips(project_id)
    transcript = json.loads(project["transcript_json"]) if project.get("transcript_json") else None
    running = project["status"] in {"queued", "transcribing", "waiting_callback"} or any(c["status"] in {"queued", "rendering"} for c in clips)
    return page(request, "project.html", project=project, clips=clips, transcript=transcript,
                transcript_text=_transcript_text(transcript) if transcript else "",
                message=request.query_params.get("message", ""), error=request.query_params.get("error", ""),
                refresh=5 if running else 0, max_clips=settings.max_clips_per_project)


@app.get("/projects/{project_id}/source")
def source_video(project_id: str, _: str = Depends(require_admin)):
    project = _project_or_404(project_id)
    path = Path(project["source_path"])
    if not path.is_file():
        raise HTTPException(404, "Source video is unavailable")
    media_type = mimetypes.guess_type(project["original_name"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.post("/projects/{project_id}/retry")
def retry_transcription(request: Request, project_id: str, csrf: str = Form(...), _: str = Depends(require_admin)):
    check_csrf(request, csrf)
    project = _project_or_404(project_id)
    if project["status"] != "failed" or project.get("transcript_json"):
        raise HTTPException(409, "Only a failed, incomplete transcription can be retried")
    DB.update_project(project_id, status="queued", error=None, callback_token=None, deepgram_request_id=None)
    return RedirectResponse(f"/projects/{project_id}?message=Transcription%20queued%20again", status_code=303)


@app.post("/projects/{project_id}/clips")
def create_clip(request: Request, project_id: str, title: str = Form(...), start_seconds: float = Form(...),
                end_seconds: float = Form(...), csrf: str = Form(...), _: str = Depends(require_admin)):
    check_csrf(request, csrf)
    project = _project_or_404(project_id)
    if project["status"] != "ready" or not project.get("transcript_json"):
        raise HTTPException(409, "Wait for transcription before adding clips")
    if DB.count_clips(project_id) >= settings.max_clips_per_project:
        raise HTTPException(409, f"This project has reached its {settings.max_clips_per_project}-clip limit")
    _validate_clip_window(start_seconds, end_seconds, float(project.get("duration") or 0))
    payload = json.loads(project["transcript_json"])
    clip_id = uuid.uuid4().hex
    DB.create_clip({"id": clip_id, "project_id": project_id, "title": title.strip()[:120] or "Custom Short",
                    "start_seconds": start_seconds, "end_seconds": end_seconds,
                    "transcript": _excerpt(payload, start_seconds, end_seconds), "score": 0})
    return RedirectResponse(f"/projects/{project_id}?message=Custom%20clip%20added", status_code=303)


def _validate_clip_window(start: float, end: float, duration: float) -> None:
    if not math.isfinite(start) or not math.isfinite(end) or not math.isfinite(duration):
        raise HTTPException(422, "Clip times must be finite numbers")
    if start < 0 or end <= start or end > duration:
        raise HTTPException(422, "Clip times must be within the source video")
    if end - start < 10 or end - start > 180:
        raise HTTPException(422, "Short clips must be between 10 and 180 seconds")


@app.post("/clips/{clip_id}/render")
def render_clip(request: Request, clip_id: str, title: str = Form(...), start_seconds: float = Form(...),
                end_seconds: float = Form(...), csrf: str = Form(...), _: str = Depends(require_admin)):
    check_csrf(request, csrf)
    clip = DB.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    project = _project_or_404(clip["project_id"])
    if project["status"] != "ready":
        raise HTTPException(409, "The transcript is not ready")
    _validate_clip_window(start_seconds, end_seconds, float(project.get("duration") or 0))
    payload = json.loads(project["transcript_json"])
    DB.update_clip(clip_id, title=title.strip()[:120] or "Short clip", start_seconds=start_seconds,
                   end_seconds=end_seconds, transcript=_excerpt(payload, start_seconds, end_seconds),
                   status="queued", output_path=None, srt_path=None, vtt_path=None, error=None)
    return RedirectResponse(f"/projects/{project['id']}?message=Clip%20queued%20for%20rendering", status_code=303)


@app.get("/clips/{clip_id}/download/{kind}")
def download_clip(clip_id: str, kind: str, _: str = Depends(require_admin)):
    clip = DB.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    fields = {"mp4": ("output_path", "video/mp4"), "srt": ("srt_path", "application/x-subrip"),
              "vtt": ("vtt_path", "text/vtt; charset=utf-8")}
    if kind not in fields:
        raise HTTPException(404, "Unknown download type")
    path_field, media_type = fields[kind]
    path = Path(clip.get(path_field) or "")
    if clip["status"] != "complete" or not path.is_file():
        raise HTTPException(404, "Rendered file is not ready")
    return FileResponse(path, filename=f"clip-{clip_id[:8]}.{kind}", media_type=media_type)


@app.post("/deepgram/callback/{project_id}")
async def deepgram_callback(request: Request, project_id: str, token: str = ""):
    project = DB.get_project(project_id)
    if not project or not project.get("callback_token") or not secrets.compare_digest(str(project["callback_token"]), token):
        raise HTTPException(404, "Callback not found")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 40 * 1024 * 1024:
            raise HTTPException(413, "Transcript callback too large")
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise HTTPException(400, "Invalid Deepgram callback JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid Deepgram callback payload")
    WORKER.accept_callback(project_id, payload)
    return JSONResponse({"received": True})


@app.get("/robots.txt")
def robots():
    return HTMLResponse("User-agent: *\nDisallow: /\n", media_type="text/plain")
