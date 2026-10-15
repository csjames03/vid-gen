"""Background worker: runs queued jobs one at a time in a thread.

One at a time on purpose: the LLM and ffmpeg each use the whole CPU, so running two jobs
in parallel would just make both slower. Progress goes to state.json (runner.py),
which the API reads.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from app.jobs import JobStore
from app.models import JobStatus

log = logging.getLogger("vid-gen.worker")


class Worker:
    def __init__(self, store: JobStore, run_job: Callable[..., object] | None = None):
        if run_job is None:
            from app.pipeline.runner import run_job as default_run
            run_job = default_run
        self.store = store
        self.run_job = run_job
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending: list[str] = []  # queue order, for "position in queue"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.current: str | None = None

    def start(self, resume: bool = True) -> None:
        if resume:  # jobs that were queued or interrupted by a server restart
            for job in reversed(self.store.list(limit=1000)):
                if job.status in (JobStatus.queued, JobStatus.running):
                    self.submit(job.id)
        self._thread = threading.Thread(target=self._loop, name="vid-gen-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout)

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._pending or job_id == self.current:
                return
            self._pending.append(job_id)
        self._queue.put(job_id)

    def position(self, job_id: str) -> int | None:
        """0 = running now, 1 = next, ...; None = not queued."""
        if job_id == self.current:
            return 0
        with self._lock:
            return self._pending.index(job_id) + 1 if job_id in self._pending else None

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                if job_id in self._pending:
                    self._pending.remove(job_id)
            self.current = job_id
            try:
                self.run_job(job_id, echo=False, store=self.store)
            except Exception:  # the runner already saved the error into state.json
                log.exception("job %s failed", job_id)
            finally:
                self.current = None
