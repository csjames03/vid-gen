"""Stage 6 - Render: scene clips -> final.mp4.

Scene clips (scenes/scene_XX.mp4, video only):
  - video asset: crop to the output size, trim to the slot (or slow down / loop if short)
  - photo:       slow Ken Burns zoom (zoompan on an upscaled copy, to avoid jitter)
  - missing:     animated dark gradient
Final: crossfade the clips, lay the narration on its slots, mix in music ducked under
the voice, burn in captions, encode for the web (+faststart).

ffmpeg always runs with cwd=job_dir and relative paths (Windows filter escaping).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.models import Aspect, AssetManifest, JobRequest, MediaType, SceneAsset, VoiceManifest
from app.pipeline import captions as captions_stage
from app.pipeline import music as music_stage
from app.pipeline import timeline
from app.pipeline.timeline import XFADE, Slot
from app.utils import ffmpeg

FINAL_FILE = "final.mp4"
SCENES_DIR = "scenes"
FPS = 30
KEN_BURNS_ZOOM = 0.12
MUSIC_VOLUME = 0.22  # before ducking
RENDER_WORKERS = 2

Log = Callable[[str], None]


# --- B7: scene clips ------------------------------------------------------------------

def _cover(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def scene_clip_cmd(asset: SceneAsset, slot: Slot, size: tuple[int, int], index: int, out: str) -> list[str]:
    """ffmpeg args (relative to the job folder) for one scene clip."""
    w, h = size
    length = slot.clip_length
    tail = f"fps={FPS},setsar=1,format=yuv420p"
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS)]

    if asset.file and asset.kind is MediaType.video:
        dur = asset.duration or 0.0
        if dur >= length:
            start = round(min(1.0, dur - length), 3)  # skip intro frames when there's room
            inp = ["-ss", str(start), "-t", f"{length:.3f}", "-i", asset.file]
            vf = f"{_cover(w, h)},{tail}"
        elif dur >= length / 1.35:
            inp = ["-i", asset.file]  # slightly short: slow it down instead of looping
            vf = f"setpts={length / dur:.4f}*PTS,{_cover(w, h)},{tail}"
        else:
            inp = ["-stream_loop", "-1", "-i", asset.file]
            vf = f"{_cover(w, h)},{tail}"
        return [*inp, "-vf", vf, "-t", f"{length:.3f}", *enc, out]

    if asset.file and asset.kind is MediaType.image:
        frames = max(1, round(length * FPS))
        # alternate zoom in / zoom out between scenes
        z = (f"1+{KEN_BURNS_ZOOM}*on/{frames}" if index % 2 == 0
             else f"{1 + KEN_BURNS_ZOOM}-{KEN_BURNS_ZOOM}*on/{frames}")
        bw, bh = (w * 3 // 2) // 2 * 2, (h * 3 // 2) // 2 * 2  # upscale first: smoother zoom
        vf = (f"{_cover(bw, bh)},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={w}x{h}:fps={FPS},{tail}")
        return ["-i", asset.file, "-vf", vf, "-frames:v", str(frames), *enc, out]

    # missing asset: slow animated gradient
    src = (f"gradients=s={w}x{h}:r={FPS}:d={length:.3f}:c0=0x0f2027:c1=0x203a43:c2=0x2c5364"
           f":nb_colors=3:speed=0.015")
    return ["-f", "lavfi", "-i", src, "-vf", tail, "-t", f"{length:.3f}", *enc, out]


def render_scenes(job_dir: Path, assets: AssetManifest, slots: list[Slot], size: tuple[int, int],
                  log: Log) -> list[str]:
    (job_dir / SCENES_DIR).mkdir(exist_ok=True)
    by_id = {a.scene_id: a for a in assets.scenes}
    jobs = []
    for i, slot in enumerate(slots):
        asset = by_id.get(slot.scene_id, SceneAsset(scene_id=slot.scene_id))
        out = f"{SCENES_DIR}/scene_{slot.scene_id:02d}.mp4"
        jobs.append((slot, asset, out, scene_clip_cmd(asset, slot, size, i, out)))

    def one(job):
        slot, asset, out, cmd = job
        ffmpeg.run(cmd, cwd=job_dir, timeout=600)
        kind = asset.kind.value if asset.kind else "placeholder"
        log(f"Scene {slot.scene_id}: rendered {slot.clip_length:.1f}s clip from {kind}")
        return out

    with ThreadPoolExecutor(RENDER_WORKERS) as pool:
        return list(pool.map(one, jobs))


# --- B8: final video -------------------------------------------------------------------

def final_graph(clips: list[str], voice: VoiceManifest, slots: list[Slot], has_music: bool,
                has_captions: bool) -> str:
    """Filtergraph. Inputs: clips 0..n-1, narration n..2n-1, music 2n (optional)."""
    n = len(clips)
    total = timeline.total(slots)
    parts: list[str] = []

    # video: chain crossfades; offset k = start of slot k (see timeline.py)
    prev = "[0:v]"
    for k in range(1, n):
        parts.append(f"{prev}[{k}:v]xfade=transition=fade:duration={XFADE}:offset={slots[k].start:.3f}[x{k}]")
        prev = f"[x{k}]"
    subs = ",subtitles=captions.ass:fontsdir=fonts" if has_captions else ""
    parts.append(f"{prev}format=yuv420p{subs}[vout]")

    # narration: each scene padded/trimmed to its slot, then concatenated
    for i, slot in enumerate(slots):
        parts.append(f"[{n + i}:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,"
                     f"atrim=0:{slot.length:.3f},asetpts=PTS-STARTPTS[a{i}]")
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[voice]")

    if has_music:
        fade_out = max(0.0, total - 2.5)
        parts += [
            f"[{2 * n}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total:.3f},"
            f"asetpts=PTS-STARTPTS,volume={MUSIC_VOLUME},afade=t=in:d=1.5,afade=t=out:st={fade_out:.3f}:d=2.5[mus]",
            "[voice]asplit=2[vmain][vsc]",
            "[mus][vsc]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]",
            "[vmain][duck]amix=inputs=2:duration=first:normalize=0[aout]",
        ]
    else:
        parts.append("[voice]anull[aout]")
    return ";\n".join(parts)


def _link_font(job_dir: Path) -> None:
    src = settings.fonts_dir / "Montserrat-ExtraBold.ttf"
    dst = job_dir / "fonts" / src.name
    if dst.exists() or not src.exists():
        return
    dst.parent.mkdir(exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def render_final(job_dir: Path, req: JobRequest, clips: list[str], voice: VoiceManifest,
                 slots: list[Slot], log: Log) -> Path:
    music = music_stage.load(job_dir) if req.music else None
    has_captions = req.captions and (job_dir / captions_stage.CAPTIONS_FILE).exists()
    if has_captions:
        _link_font(job_dir)
    total = timeline.total(slots)

    graph_file = "final_graph.txt"
    (job_dir / graph_file).write_text(
        final_graph(clips, voice, slots, music is not None, has_captions), encoding="utf-8")

    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", c]
    for a in voice.scenes:
        inputs += ["-i", a.file]
    if music:
        inputs += ["-stream_loop", "-1", "-i", music.file]

    ffmpeg.run([*inputs, "-/filter_complex", graph_file, "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-t", f"{total:.3f}", FINAL_FILE], cwd=job_dir, timeout=1800)
    info = ffmpeg.probe(FINAL_FILE, cwd=job_dir)
    log(f"Final video: {info.width}x{info.height}, {info.duration:.1f}s"
        + (", captions" if has_captions else "") + (f", music {music.title!r}" if music else ""))
    return job_dir / FINAL_FILE


def run(job_dir: Path, req: JobRequest, assets: AssetManifest, voice: VoiceManifest,
        log: Log = print, size: tuple[int, int] | None = None) -> Path:
    size = size or Aspect(req.aspect).size
    slots = timeline.build(voice)
    log(f"Rendering {len(slots)} scenes at {size[0]}x{size[1]}, {timeline.total(slots):.1f}s")
    clips = render_scenes(job_dir, assets, slots, size, log)
    return render_final(job_dir, req, clips, voice, slots, log)
