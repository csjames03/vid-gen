"""FastAPI app: create jobs, follow their progress, fetch the results.

    uv run uvicorn app.main:app --port 8000        (docs at http://localhost:8000/docs)
"""

from __future__ import annotations

import asyncio
import re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.jobs import JobNotFound, JobStore, store as default_store
from app.models import (STAGE_ORDER, JobRequest, JobState, JobStatus, MediaType, SceneAsset, Script,
                        StageStatus)
from app.pipeline import assets as assets_stage
from app.pipeline import render, script as script_stage, voice as voice_stage
from app.utils import ffmpeg
from app.worker import Worker

SSE_POLL_SECONDS = 0.5
SSE_KEEPALIVE_SECONDS = 15
FALLBACK_VOICES = [
    {"name": "en-US-AndrewNeural", "gender": "Male", "locale": "en-US"},
    {"name": "en-US-AriaNeural", "gender": "Female", "locale": "en-US"},
    {"name": "en-US-GuyNeural", "gender": "Male", "locale": "en-US"},
    {"name": "en-US-JennyNeural", "gender": "Female", "locale": "en-US"},
    {"name": "en-GB-SoniaNeural", "gender": "Female", "locale": "en-GB"},
    {"name": "en-GB-RyanNeural", "gender": "Male", "locale": "en-GB"},
]


# --- response models -------------------------------------------------------------------

class JobCreated(BaseModel):
    id: str
    status: JobStatus
    queue_position: int | None


class JobSummary(BaseModel):
    id: str
    topic: str
    title: str | None
    status: JobStatus
    progress: float
    aspect: str
    duration: float | None  # of the final video, when done
    created_at: datetime
    poster_url: str | None
    video_url: str | None


class JobDetail(JobState):
    queue_position: int | None = None
    title: str | None = None


class SceneView(BaseModel):
    id: int
    narration: str
    visual_description: str
    search_queries: list[str]
    media_type: MediaType
    asset: SceneAsset | None = None
    audio_duration: float | None = None
    thumbnail_url: str | None = None


class Health(BaseModel):
    ok: bool
    checks: dict[str, bool]
    details: dict[str, str]


# --- app ---------------------------------------------------------------------------------

def create_app(store: JobStore | None = None, worker: Worker | None = None,
               start_worker: bool = True) -> FastAPI:
    store = store or default_store
    worker = worker or Worker(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="vid-gen API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.api_cors_origins,
                       allow_methods=["*"], allow_headers=["*"])
    app.state.store, app.state.worker = store, worker

    def load(job_id: str) -> JobState:
        try:
            return store.load(job_id)
        except JobNotFound:
            raise HTTPException(404, f"job {job_id} not found") from None

    def job_file(job_id: str, rel: str) -> Path:
        path = store.job_dir(job_id) / rel
        if not path.is_file():
            raise HTTPException(404, f"{rel} not available")
        return path

    def title_of(job_id: str) -> str | None:
        try:
            return script_stage.load(store.job_dir(job_id)).title
        except (FileNotFoundError, ValueError):
            return None

    # --- C1: health --------------------------------------------------------------------

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=3) as http:
                r = await http.get(f"{settings.ollama_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            checks["ollama"] = True
            checks["ollama_model"] = settings.ollama_model in models
            details["ollama_model"] = settings.ollama_model if checks["ollama_model"] else \
                f"{settings.ollama_model} not pulled (have: {', '.join(models) or 'none'})"
        except (httpx.HTTPError, ValueError) as e:
            checks["ollama"] = checks["ollama_model"] = False
            details["ollama"] = f"not reachable at {settings.ollama_url}: {e}"
        try:
            details["ffmpeg"] = ffmpeg.find_binary("ffmpeg")
            checks["ffmpeg"] = True
        except FileNotFoundError as e:
            checks["ffmpeg"], details["ffmpeg"] = False, str(e)
        checks["pixabay_key"] = bool(settings.pixabay_api_key)
        tracks = [p for p in settings.music_dir.rglob("*") if p.suffix.lower() in {".mp3", ".m4a", ".wav"}] \
            if settings.music_dir.exists() else []
        checks["music"] = bool(tracks)
        details["music"] = f"{len(tracks)} tracks in {settings.music_dir}"
        checks["caption_font"] = (settings.fonts_dir / "Montserrat-ExtraBold.ttf").exists()
        return Health(ok=all(checks.values()), checks=checks, details=details)

    @app.get("/voices")
    async def voices() -> list[dict]:
        if not hasattr(app.state, "voices"):
            try:
                app.state.voices = await asyncio.wait_for(voice_stage.list_voices(), timeout=10)
            except Exception:
                return FALLBACK_VOICES  # offline: don't cache, try again next time
        return app.state.voices

    # --- C2: jobs ------------------------------------------------------------------------

    @app.post("/jobs", response_model=JobCreated, status_code=201)
    def create_job(req: JobRequest) -> JobCreated:
        state = store.create(req)
        worker.submit(state.id)
        return JobCreated(id=state.id, status=state.status, queue_position=worker.position(state.id))

    @app.get("/jobs", response_model=list[JobSummary])
    def list_jobs(limit: int = 50) -> list[JobSummary]:
        out = []
        for j in store.list(limit=limit):
            done = j.status is JobStatus.done
            duration = None
            if done:
                duration = _video_duration(store.job_dir(j.id) / render.FINAL_FILE)
                done = duration is not None
            out.append(JobSummary(
                id=j.id, topic=j.request.topic, title=title_of(j.id), status=j.status,
                progress=j.progress, aspect=j.request.aspect.value, duration=duration,
                created_at=j.created_at, poster_url=f"/jobs/{j.id}/poster" if done else None,
                video_url=f"/jobs/{j.id}/video" if done else None))
        return out

    @app.get("/jobs/{job_id}", response_model=JobDetail)
    def get_job(job_id: str) -> JobDetail:
        state = load(job_id)
        return JobDetail(**state.model_dump(), queue_position=worker.position(job_id),
                         title=title_of(job_id))

    @app.post("/jobs/{job_id}/retry", response_model=JobCreated)
    def retry_job(job_id: str, from_stage: str | None = None) -> JobCreated:
        """Resume a failed job (or re-run it from a stage, e.g. ?from_stage=render)."""
        state = load(job_id)
        if worker.position(job_id) is not None:
            raise HTTPException(409, "job is already queued or running")
        if from_stage:
            stages = [s.value for s in STAGE_ORDER]
            if from_stage not in stages:
                raise HTTPException(422, f"from_stage must be one of {stages}")
            for s in STAGE_ORDER[stages.index(from_stage):]:
                state.stages[s] = StageStatus.pending
        state.status, state.error = JobStatus.queued, None
        state.add_log("Re-queued" + (f" from {from_stage}" if from_stage else ""))
        store.save(state)
        worker.submit(job_id)
        return JobCreated(id=job_id, status=state.status, queue_position=worker.position(job_id))

    @app.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str) -> None:
        load(job_id)
        if worker.position(job_id) is not None:
            raise HTTPException(409, "job is queued or running")
        shutil.rmtree(store.job_dir(job_id))

    # --- C3: live progress (Server-Sent Events) --------------------------------------------

    @app.get("/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        load(job_id)

        async def stream():
            last_sent, idle = None, 0.0
            while True:
                if await request.is_disconnected():
                    return
                try:
                    state = store.load(job_id)
                except JobNotFound:
                    yield "event: deleted\ndata: {}\n\n"
                    return
                detail = JobDetail(**state.model_dump(), queue_position=worker.position(job_id),
                                   title=title_of(job_id))
                payload = detail.model_dump_json()
                if payload != last_sent:
                    yield f"event: state\ndata: {payload}\n\n"
                    last_sent, idle = payload, 0.0
                    if state.status in (JobStatus.done, JobStatus.failed):
                        return
                elif idle >= SSE_KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    idle = 0.0
                await asyncio.sleep(SSE_POLL_SECONDS)
                idle += SSE_POLL_SECONDS

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- C4: results ---------------------------------------------------------------------

    @app.get("/jobs/{job_id}/video")
    def job_video(job_id: str, download: bool = False) -> FileResponse:
        path = job_file(job_id, render.FINAL_FILE)
        name = None
        if download:
            slug = re.sub(r"[^a-z0-9]+", "-", (title_of(job_id) or load(job_id).request.topic).lower()).strip("-")
            name = f"{slug or 'video'}.mp4"
        return FileResponse(path, media_type="video/mp4", filename=name,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/jobs/{job_id}/poster")
    def job_poster(job_id: str) -> FileResponse:
        job_dir = store.job_dir(job_id)
        poster = job_dir / "thumbs" / "poster.jpg"
        if not poster.exists():
            job_file(job_id, render.FINAL_FILE)
            _thumbnail(job_dir, render.FINAL_FILE, poster, at=1.0)
        return FileResponse(poster, media_type="image/jpeg")

    @app.get("/jobs/{job_id}/script", response_model=Script)
    def job_script(job_id: str) -> Script:
        load(job_id)
        try:
            return script_stage.load(store.job_dir(job_id))
        except FileNotFoundError:
            raise HTTPException(404, "script not generated yet") from None

    @app.get("/jobs/{job_id}/scenes", response_model=list[SceneView])
    def job_scenes(job_id: str) -> list[SceneView]:
        """Script scenes merged with their chosen asset and narration length."""
        load(job_id)
        job_dir = store.job_dir(job_id)
        try:
            script = script_stage.load(job_dir)
        except FileNotFoundError:
            return []
        assets = {a.scene_id: a for a in _try(lambda: assets_stage.load(job_dir).scenes, [])}
        audio = {a.scene_id: a.duration for a in _try(lambda: voice_stage.load(job_dir).scenes, [])}
        return [SceneView(
            id=s.id, narration=s.narration, visual_description=s.visual_description,
            search_queries=s.search_queries, media_type=s.media_type, asset=assets.get(s.id),
            audio_duration=audio.get(s.id),
            thumbnail_url=f"/jobs/{job_id}/scenes/{s.id}/thumbnail" if assets.get(s.id) and assets[s.id].file else None,
        ) for s in script.scenes]

    @app.get("/jobs/{job_id}/scenes/{scene_id}/thumbnail")
    def scene_thumbnail(job_id: str, scene_id: int) -> FileResponse:
        load(job_id)
        job_dir = store.job_dir(job_id)
        asset = next((a for a in _try(lambda: assets_stage.load(job_dir).scenes, []) if a.scene_id == scene_id), None)
        if not asset or not asset.file:
            raise HTTPException(404, "no asset for this scene")
        thumb = job_dir / "thumbs" / f"scene_{scene_id:02d}.jpg"
        if not thumb.exists():
            _thumbnail(job_dir, asset.file, thumb, at=1.0 if asset.kind is MediaType.video else None)
        return FileResponse(thumb, media_type="image/jpeg")

    return app


def _thumbnail(job_dir: Path, src: str, dest: Path, at: float | None) -> None:
    dest.parent.mkdir(exist_ok=True)
    seek = ["-ss", str(at)] if at is not None else []
    rel = dest.relative_to(job_dir).as_posix()
    ffmpeg.run([*seek, "-i", src, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", rel],
               cwd=job_dir, timeout=60)


_durations: dict[tuple[str, float], float] = {}


def _video_duration(path: Path) -> float | None:
    """ffprobe is ~50ms per file; cache by (path, mtime) so listing jobs stays fast."""
    try:
        key = (str(path), path.stat().st_mtime)
    except FileNotFoundError:
        return None
    if key not in _durations:
        try:
            _durations[key] = round(ffmpeg.probe_duration(path), 1)
        except ffmpeg.FFmpegError:
            return None
    return _durations[key]


def _try(fn, default):
    try:
        return fn()
    except (FileNotFoundError, ValueError):
        return default


app = create_app()
