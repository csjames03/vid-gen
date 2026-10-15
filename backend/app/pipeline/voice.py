"""Stage 3 - Voice: one narration mp3 per scene + word timings, via edge-tts.

edge-tts is free and needs no key, but it calls Microsoft's online TTS service, so calls
are retried. It reports every word's offset, which the captions stage uses directly
(no speech recognition needed).

Outputs: audio/scene_XX.mp3, audio/scene_XX.words.json, voice.json
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import edge_tts

from app.config import settings
from app.models import JobRequest, SceneAudio, Script, VoiceManifest, WordTiming
from app.utils import ffmpeg

VOICE_FILE = "voice.json"
AUDIO_DIR = "audio"
TICKS_PER_SECOND = 10_000_000  # edge-tts offsets are in 100ns units
CONCURRENCY = 3
RETRIES = 3

Log = Callable[[str], None]


class VoiceError(RuntimeError):
    pass


async def synthesize(text: str, voice: str, out_mp3: Path, rate: str = "+0%") -> list[WordTiming]:
    """Write `text` as speech to `out_mp3`; return per-word timings."""
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
            audio = bytearray()
            words: list[WordTiming] = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / TICKS_PER_SECOND
                    words.append(WordTiming(text=chunk["text"], start=round(start, 3),
                                            end=round(start + chunk["duration"] / TICKS_PER_SECOND, 3)))
            if not audio:
                raise VoiceError("edge-tts returned no audio")
            out_mp3.write_bytes(bytes(audio))
            return words
        except Exception as e:  # network errors, service hiccups, empty responses
            last_err = e
            if attempt < RETRIES:
                await asyncio.sleep(2 * attempt)
    raise VoiceError(f"edge-tts failed after {RETRIES} attempts: {last_err}") from last_err


async def _run_all(job_dir: Path, script: Script, voice: str, log: Log) -> list[SceneAudio]:
    (job_dir / AUDIO_DIR).mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(scene) -> SceneAudio:
        mp3 = f"{AUDIO_DIR}/scene_{scene.id:02d}.mp3"
        words_file = f"{AUDIO_DIR}/scene_{scene.id:02d}.words.json"
        async with sem:
            words = await synthesize(scene.narration, voice, job_dir / mp3, settings.tts_rate)
        (job_dir / words_file).write_text(
            json.dumps([w.model_dump() for w in words], indent=1), encoding="utf-8")
        duration = round(ffmpeg.probe_duration(mp3, cwd=job_dir), 3)
        last = words[-1].end if words else 0.0
        log(f"Scene {scene.id}: {duration:.1f}s audio, {len(words)} words (last word ends {last:.1f}s)")
        return SceneAudio(scene_id=scene.id, file=mp3, words_file=words_file,
                          duration=duration, voice=voice)

    return list(await asyncio.gather(*(one(s) for s in script.scenes)))


def run(job_dir: Path, req: JobRequest, script: Script, log: Log = print) -> VoiceManifest:
    voice = req.voice or settings.tts_voice
    log(f"Voice: {voice}, rate {settings.tts_rate}")
    manifest = VoiceManifest(scenes=asyncio.run(_run_all(job_dir, script, voice, log)))
    (job_dir / VOICE_FILE).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    log(f"Voice ready: {len(manifest.scenes)} scenes, {manifest.total_duration:.1f}s total")
    return manifest


def load(job_dir: Path) -> VoiceManifest:
    return VoiceManifest.model_validate_json((job_dir / VOICE_FILE).read_text(encoding="utf-8"))


def load_words(job_dir: Path, scene: SceneAudio) -> list[WordTiming]:
    data = json.loads((job_dir / scene.words_file).read_text(encoding="utf-8"))
    return [WordTiming.model_validate(w) for w in data]


async def list_voices(locale_prefix: str = "en-") -> list[dict]:
    """For the frontend's voice picker."""
    return [{"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
            for v in await edge_tts.list_voices() if v["Locale"].startswith(locale_prefix)]
