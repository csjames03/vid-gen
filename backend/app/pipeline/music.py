"""Stage 5 - Music: pick a background track for the script's mood from MUSIC_DIR/<mood>/.

The choice is seeded with the job id, so re-running a job picks the same track.
Trimming, looping, fading and ducking under the voice happen in the render stage.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.models import MusicChoice, MusicMood

MUSIC_FILE = "music.json"
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}

Log = Callable[[str], None]


def _tracks(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTS)


def pick(mood: MusicMood, seed: str, music_dir: Path | None = None) -> MusicChoice | None:
    music_dir = music_dir or settings.music_dir
    rng = random.Random(seed)
    tracks = _tracks(music_dir / mood.value)
    matched = bool(tracks)
    if not tracks:
        tracks = _tracks(music_dir)  # any mood is better than silence
    if not tracks:
        return None
    t = rng.choice(tracks)
    return MusicChoice(file=str(t.resolve()), mood=mood, matched_mood=matched, title=t.stem)


def run(job_dir: Path, mood: MusicMood, log: Log = print) -> MusicChoice | None:
    choice = pick(mood, seed=job_dir.name)
    path = job_dir / MUSIC_FILE
    if choice is None:
        path.unlink(missing_ok=True)
        log(f"No music found in {settings.music_dir}, video will have narration only")
        return None
    path.write_text(choice.model_dump_json(indent=2), encoding="utf-8")
    log(f"Music: {choice.title!r} ({mood}" + ("" if choice.matched_mood else ", no track for this mood") + ")")
    return choice


def load(job_dir: Path) -> MusicChoice | None:
    path = job_dir / MUSIC_FILE
    return MusicChoice.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None
