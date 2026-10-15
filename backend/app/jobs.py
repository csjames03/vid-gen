"""Job storage on disk: one folder per job under data/jobs/{id}/ with a state.json.

Layout of a job folder (stages create their own subfolders):
    state.json      JobState (status, per-stage progress, log)
    script.json     stage 1 output
    assets/ audio/ scenes/ captions.ass music.mp3 final.mp4

The queue + worker thread live in this module too.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.models import JobRequest, JobState

STATE_FILE = "state.json"
_JOB_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class JobNotFound(LookupError):
    pass


def new_job_id() -> str:
    """Sortable by creation time: 20261015-143012-ab12cd"""
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()  # worker thread and API handlers both write state

    def job_dir(self, job_id: str) -> Path:
        # job ids come from URLs, so validate them before touching the filesystem
        if not _JOB_ID_RE.match(job_id):
            raise JobNotFound(job_id)
        return self.root / job_id

    def create(self, request: JobRequest) -> JobState:
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            job_id = new_job_id()
            try:
                self.job_dir(job_id).mkdir()
                break
            except FileExistsError:  # same second + same random suffix, try again
                continue
        state = JobState(id=job_id, request=request)
        state.add_log(f"Job created: {request.topic!r}")
        self.save(state)
        return state

    def load(self, job_id: str) -> JobState:
        path = self.job_dir(job_id) / STATE_FILE
        for attempt in range(5):
            try:
                return JobState.model_validate_json(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise JobNotFound(job_id) from None
            except PermissionError:  # Windows: the worker is swapping state.json right now
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise AssertionError("unreachable")

    def save(self, state: JobState) -> None:
        """Atomic write: write a temp file, then swap it in, so readers never see half a file."""
        state.updated_at = datetime.now(state.updated_at.tzinfo)
        path = self.job_dir(state.id) / STATE_FILE
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            _replace_with_retry(tmp, path)

    def list(self, limit: int = 50) -> list[JobState]:
        """Newest first. Skips folders with a missing or broken state.json."""
        if not self.root.exists():
            return []
        ids = sorted((p.name for p in self.root.iterdir() if _JOB_ID_RE.match(p.name)), reverse=True)
        jobs: list[JobState] = []
        for job_id in ids:
            try:
                jobs.append(self.load(job_id))
            except (JobNotFound, ValueError):
                continue
            if len(jobs) >= limit:
                break
        return jobs


def _replace_with_retry(src: Path, dst: Path, attempts: int = 5) -> None:
    # On Windows, os.replace fails if another process (e.g. the API reading state.json)
    # has the file open at that moment. It's brief, so retry a few times.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


store = JobStore(settings.jobs_dir)
