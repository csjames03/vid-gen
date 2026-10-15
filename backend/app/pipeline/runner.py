"""Runs a job's stages in order, keeping state.json up to date.

Stages already done (and whose output file still exists) are skipped, so a failed or
interrupted job resumes where it stopped. `from_stage` forces a re-run from that stage.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from app.jobs import JobStore, store as default_store
from app.models import STAGE_ORDER, JobRequest, JobState, JobStatus, Stage, StageStatus
from app.pipeline import assets, captions, music, render, script, voice

# rough share of total run time, for the progress bar
WEIGHTS = {Stage.script: 0.35, Stage.assets: 0.2, Stage.voice: 0.08,
           Stage.captions: 0.02, Stage.music: 0.01, Stage.render: 0.34}

OUTPUTS = {Stage.script: script.SCRIPT_FILE, Stage.assets: assets.ASSETS_FILE,
           Stage.voice: voice.VOICE_FILE, Stage.captions: captions.CAPTIONS_FILE,
           Stage.music: music.MUSIC_FILE, Stage.render: render.FINAL_FILE}

Log = Callable[[str], None]


def run_stage(stage: Stage, job_dir: Path, req: JobRequest, log: Log) -> StageStatus:
    if stage is Stage.script:
        script.run(job_dir, req, log=log)
    elif stage is Stage.assets:
        assets.run(job_dir, req, script.load(job_dir), log=log)
    elif stage is Stage.voice:
        voice.run(job_dir, req, script.load(job_dir), log=log)
    elif stage is Stage.captions:
        if not req.captions:
            return StageStatus.skipped
        captions.run(job_dir, req, script.load(job_dir), voice.load(job_dir), log=log)
    elif stage is Stage.music:
        if not req.music:
            return StageStatus.skipped
        if music.run(job_dir, script.load(job_dir).music_mood, log=log) is None:
            return StageStatus.skipped
    elif stage is Stage.render:
        render.run(job_dir, req, assets.load(job_dir), voice.load(job_dir), log=log)
    return StageStatus.done


def _is_complete(state: JobState, stage: Stage, job_dir: Path) -> bool:
    status = state.stages[stage]
    if status is StageStatus.skipped:
        return True
    return status is StageStatus.done and (job_dir / OUTPUTS[stage]).exists()


def run_job(job_id: str, from_stage: Stage | None = None, echo: bool = True,
            store: JobStore = default_store) -> JobState:
    state = store.load(job_id)
    job_dir = store.job_dir(job_id)

    def log(msg: str) -> None:
        if echo:
            print(msg, flush=True)
        state.add_log(msg)
        store.save(state)

    if from_stage is not None:
        for s in STAGE_ORDER[STAGE_ORDER.index(from_stage):]:
            state.stages[s] = StageStatus.pending

    state.status, state.error = JobStatus.running, None
    store.save(state)
    started = time.time()
    current: Stage | None = None
    try:
        for stage in STAGE_ORDER:
            current = stage
            if _is_complete(state, stage, job_dir):
                state.progress = round(state.progress + WEIGHTS[stage], 3)
                continue
            state.stage, state.stages[stage] = stage, StageStatus.running
            log(f"== {stage.value} ==")
            t = time.time()
            state.stages[stage] = run_stage(stage, job_dir, state.request, log)
            state.progress = min(1.0, round(state.progress + WEIGHTS[stage], 3))
            log(f"{stage.value} {state.stages[stage].value} in {time.time() - t:.0f}s")
    except Exception as e:
        if current is not None:
            state.stages[current] = StageStatus.failed
        state.status, state.error = JobStatus.failed, f"{type(e).__name__}: {e}"
        log(f"FAILED in {current.value if current else '?'}: {state.error}")
        raise
    state.status, state.stage, state.progress = JobStatus.done, None, 1.0
    state.output_url = f"/jobs/{job_id}/video"
    log(f"Done in {time.time() - started:.0f}s -> {job_dir / render.FINAL_FILE}")
    return state
