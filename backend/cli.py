"""Command-line entry point.

Full pipeline (topic -> mp4):
    uv run python cli.py run "why octopuses are basically aliens" --duration 30 --aspect 9:16
Resume / re-run an existing job (optionally from a stage):
    uv run python cli.py run --job 20261015-011105-7d44a7 --from render
Run a single stage of an existing job:
    uv run python cli.py captions --job 20261015-011105-7d44a7
"""

import argparse
import sys

from app.jobs import store
from app.models import JobRequest, Stage
from app.pipeline import runner


def main() -> None:
    p = argparse.ArgumentParser(description="vid-gen: topic -> video")
    p.add_argument("command", choices=["run", *[s.value for s in Stage]])
    p.add_argument("topic", nargs="?")
    p.add_argument("--job", help="existing job id")
    p.add_argument("--from", dest="from_stage", choices=[s.value for s in Stage],
                   help="with run --job: re-run from this stage")
    p.add_argument("--duration", type=int, choices=[30, 60, 90], default=60)
    p.add_argument("--aspect", choices=["9:16", "16:9"], default="9:16")
    p.add_argument("--tone", choices=["fun", "informative", "dramatic"], default="informative")
    p.add_argument("--voice", help="edge-tts voice, e.g. en-US-AriaNeural")
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--no-music", action="store_true")
    args = p.parse_args()

    if args.job:
        state = store.load(args.job)
    elif args.topic:
        state = store.create(JobRequest(
            topic=args.topic, duration_sec=args.duration, aspect=args.aspect, tone=args.tone,
            voice=args.voice, captions=not args.no_captions, music=not args.no_music))
    else:
        sys.exit("give a topic or --job ID")
    print(f"job {state.id} -> {store.job_dir(state.id)}", flush=True)

    if args.command == "run":
        from_stage = Stage(args.from_stage) if args.from_stage else None
        try:
            runner.run_job(state.id, from_stage=from_stage)
        except Exception:
            sys.exit(1)  # already logged + saved in state.json
    else:
        _single(state.id, Stage(args.command))


def _single(job_id: str, stage: Stage) -> None:
    state = store.load(job_id)

    def log(msg: str) -> None:
        print(msg, flush=True)
        state.add_log(msg)
        store.save(state)

    state.stages[stage] = runner.run_stage(stage, store.job_dir(job_id), state.request, log)
    store.save(state)
    print(f"{stage.value}: {state.stages[stage].value}")


if __name__ == "__main__":
    main()
