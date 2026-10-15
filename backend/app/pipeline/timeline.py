"""Where each scene sits in the final video. Shared by captions and render.

Each scene gets a slot = its narration length + a short pause. Scene clips are rendered
XFADE seconds longer than their slot (except the last) and crossfaded inside that
overlap, so scene i's picture *and* narration both start exactly at slot.start.
Captions are then simply slot.start + word offset.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import VoiceManifest

GAP = 0.25  # pause after each scene's narration
XFADE = 0.4  # crossfade duration between scenes


@dataclass(frozen=True)
class Slot:
    scene_id: int
    start: float
    length: float  # narration + GAP
    clip_length: float  # length of the rendered scene clip (slot + crossfade overlap)


def build(voice: VoiceManifest) -> list[Slot]:
    slots, t = [], 0.0
    n = len(voice.scenes)
    for i, a in enumerate(voice.scenes):
        length = round(a.duration + GAP, 3)
        clip = round(length + (XFADE if i < n - 1 else 0.0), 3)
        slots.append(Slot(scene_id=a.scene_id, start=round(t, 3), length=length, clip_length=clip))
        t += length
    return slots


def total(slots: list[Slot]) -> float:
    return round(slots[-1].start + slots[-1].length, 3) if slots else 0.0
