"""Stage 4 - Captions: word timings -> captions.ass (burned in by the render stage).

Style: big bold uppercase words, 1-3 per caption, the word being spoken highlighted.
edge-tts timings come without punctuation, so we re-attach it from the script text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.models import Aspect, JobRequest, Script, VoiceManifest, WordTiming
from app.pipeline import timeline, voice

CAPTIONS_FILE = "captions.ass"
FONT_NAME = "Montserrat ExtraBold"
HIGHLIGHT = "&H0000E5FF&"  # ASS colours are BGR: this is a warm yellow
MAX_WORDS = 3
MAX_CHARS = 16
HOLD_AFTER_LAST = 0.3  # keep the last caption of a scene up a bit after the word ends

Log = Callable[[str], None]


@dataclass
class Word:
    text: str  # display text, with punctuation
    start: float  # seconds in the final video
    end: float


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def attach_punctuation(words: list[WordTiming], narration: str) -> list[str]:
    """Map each timed word back to the matching token of the narration (with punctuation)."""
    tokens = narration.split()
    out, j = [], 0
    for w in words:
        target = _norm(w.text)
        for k in range(j, min(j + 4, len(tokens))):
            if _norm(tokens[k]) == target:
                out.append(tokens[k])
                j = k + 1
                break
        else:
            out.append(w.text)
    return out


def chunk(words: list[Word]) -> list[list[Word]]:
    """1-3 words per caption, breaking after punctuation."""
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(cur) >= MAX_WORDS or len(" ".join(x.text for x in cur + [w])) > MAX_CHARS):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w.text[-1:] in ".,!?;:":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return re.sub(r"[{}\\]", "", text).upper()


def style(aspect: Aspect) -> dict:
    if aspect is Aspect.portrait:
        return {"size": 92, "outline": 7, "shadow": 3, "margin_v": 560, "margin_h": 60}
    return {"size": 76, "outline": 6, "shadow": 3, "margin_v": 110, "margin_h": 120}


def build_ass(scene_words: list[list[Word]], scene_ends: list[float], aspect: Aspect) -> str:
    w, h = aspect.size
    st = style(aspect)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{FONT_NAME},{st['size']},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,"
        f"0,0,0,0,100,100,1,0,1,{st['outline']},{st['shadow']},2,{st['margin_h']},{st['margin_h']},"
        f"{st['margin_v']},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for words, scene_end in zip(scene_words, scene_ends):
        chunks = chunk(words)
        for ci, ch in enumerate(chunks):
            nxt = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
            ch_end = nxt if nxt is not None and nxt - ch[-1].end < 0.6 else ch[-1].end + HOLD_AFTER_LAST
            ch_end = min(ch_end, scene_end)
            # one event per word, so the spoken word is highlighted
            for wi, word in enumerate(ch):
                start = ch[0].start if wi == 0 else word.start
                end = ch[wi + 1].start if wi + 1 < len(ch) else ch_end
                if end <= start:
                    continue
                text = " ".join(
                    f"{{\\c{HIGHLIGHT}}}{_clean(x.text)}{{\\c&H00FFFFFF&}}" if x is word else _clean(x.text)
                    for x in ch)
                lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def run(job_dir: Path, req: JobRequest, script: Script, voice_m: VoiceManifest, log: Log = print) -> Path:
    slots = {s.scene_id: s for s in timeline.build(voice_m)}
    narration = {s.id: s.narration for s in script.scenes}
    scene_words, scene_ends = [], []
    for a in voice_m.scenes:
        slot = slots[a.scene_id]
        timed = voice.load_words(job_dir, a)
        texts = attach_punctuation(timed, narration.get(a.scene_id, ""))
        scene_words.append([Word(t, slot.start + w.start, slot.start + w.end) for t, w in zip(texts, timed)])
        scene_ends.append(slot.start + slot.length)
    path = job_dir / CAPTIONS_FILE
    path.write_text(build_ass(scene_words, scene_ends, req.aspect), encoding="utf-8")
    n = sum(len(chunk(ws)) for ws in scene_words)
    log(f"Captions ready: {n} captions, {sum(map(len, scene_words))} words")
    return path
