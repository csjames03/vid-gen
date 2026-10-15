"""Stage 1 - Script: topic -> script.json, written by the local Ollama model.

Small local models (3B) return valid JSON reliably when the schema is enforced, but they
tend to write far too little narration. So:
  1. one structured call with the Script JSON schema + explicit word targets,
  2. check the result and retry with concrete feedback (up to `max_attempts`),
  3. if still short, keep the best attempt and expand thin scenes one by one with small
     plain-text calls (much easier for a small model than rewriting the whole JSON).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import ollama
from pydantic import ValidationError

from app.config import settings
from app.models import JobRequest, Script, Tone

SCRIPT_FILE = "script.json"
WORDS_PER_MINUTE = 150
SCENES_FOR_DURATION = {30: 5, 60: 6, 90: 8}

Log = Callable[[str], None]


class ScriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Targets:
    total_words: int
    scenes: int

    @property
    def words_per_scene(self) -> int:
        return round(self.total_words / self.scenes)

    @classmethod
    def for_duration(cls, seconds: int) -> Targets:
        return cls(total_words=round(seconds / 60 * WORDS_PER_MINUTE),
                   scenes=SCENES_FOR_DURATION.get(seconds, max(3, round(seconds / 12))))


# --- prompt -------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a scriptwriter for short faceless videos (YouTube Shorts, TikTok, Reels).
You write narration that is spoken aloud by a voiceover, over stock footage.

Rules:
- narration: plain spoken sentences only. No scene labels, no stage directions, no emojis, no hashtags, no markdown.
- Scene 1 opens with a strong hook. The last scene ends with a memorable closing line.
- Use real, specific facts. Short sentences that are easy to say out loud.
- search_queries: 1 to 3 SHORT stock-footage searches (1-3 words each) naming concrete, filmable things,
  most specific first, then more generic. Good: "octopus", "coral reef", "scuba diver".
  Bad: "octopus intelligence", "nature's masters", "mysterious ocean vibes".
- media_type: "video" for things that move, "image" for things better shown as a photo (maps, artworks, historic people).
- music_mood: one of upbeat, calm, mysterious, epic, sad, corporate."""

TONE_HINTS = {
    Tone.fun: "playful, surprising and light, like an enthusiastic friend",
    Tone.informative: "clear, confident and educational, like a good documentary",
    Tone.dramatic: "suspenseful and cinematic, building tension",
}

EXAMPLE_SCENE = {
    "id": 1,
    "narration": "An octopus has three hearts, blue blood, and nine brains. One brain sits in its head, "
                 "and every single arm has a mini brain of its own that can taste and think.",
    "visual_description": "close-up of an octopus crawling over a coral reef",
    "search_queries": ["octopus", "octopus close up", "coral reef"],
    "media_type": "video",
}


def build_prompt(req: JobRequest, t: Targets) -> str:
    return f"""Write a {req.duration_sec}-second video script about: {req.topic}

Tone: {TONE_HINTS[req.tone]}.
Length: exactly {t.scenes} scenes. Each scene's narration must be {t.words_per_scene - 5}-{t.words_per_scene + 5} words \
(2-3 full sentences). Total narration about {t.total_words} words.

Example of ONE scene at the right length (for a different topic):
{json.dumps(EXAMPLE_SCENE, indent=2)}

Return only the JSON object."""


# --- clean-up + checks -------------------------------------------------------------

_LABEL_RE = re.compile(r"^\s*(scene\s*\d+|narrator|narration|voice ?over|vo)\s*[:\-–]\s*", re.I)
_BRACKETS_RE = re.compile(r"\[[^\]]*\]|\([^)]*(music|sfx|pause|cut to)[^)]*\)", re.I)
_NON_SPEECH_RE = re.compile(r"[*_#`~]|[\U0001F000-\U0001FAFF☀-➿]")
_QUERY_PUNCT_RE = re.compile(r"[^\w\s'-]")


def clean_narration(text: str) -> str:
    text = _BRACKETS_RE.sub(" ", text)
    text = _NON_SPEECH_RE.sub("", text)
    text = _LABEL_RE.sub("", text)
    text = " ".join(text.split()).strip(" \"'")
    return text


def clean_query(q: str, max_words: int = 4) -> str:
    words = _QUERY_PUNCT_RE.sub(" ", q.lower()).split()
    return " ".join(words[:max_words])


_BAD_TITLE_RE = re.compile(r"^\s*(scene|part|chapter|video|title)?\s*\d*\s*$", re.I)


def fix_title(title: str, topic: str) -> str:
    """Small models sometimes return "Scene 1" or nothing as the title; fall back to the topic."""
    title = title.strip(" \"'")
    if not title or _BAD_TITLE_RE.match(title):
        return topic[:1].upper() + topic[1:]
    return title


def tidy(script: Script, topic: str = "") -> Script:
    """Normalize LLM output: renumber scenes, clean narration and search queries."""
    scenes = []
    for i, sc in enumerate(script.scenes, start=1):
        queries: list[str] = []
        for q in sc.search_queries:
            cq = clean_query(q)
            if cq and cq not in queries:
                queries.append(cq)
        if not queries:  # fall back to the start of the visual description
            queries = [clean_query(sc.visual_description, 3) or "abstract background"]
        scenes.append(sc.model_copy(update={
            "id": i,
            "narration": clean_narration(sc.narration),
            "search_queries": queries[:3],
        }))
    return script.model_copy(update={"title": fix_title(script.title, topic or script.title), "scenes": scenes})


def problems(script: Script, t: Targets) -> list[str]:
    out = []
    if script.word_count < 0.8 * t.total_words:
        out.append(f"The narration is only {script.word_count} words in total, but it must be about "
                   f"{t.total_words} words. Make every scene longer.")
    elif script.word_count > 1.4 * t.total_words:
        out.append(f"The narration is {script.word_count} words, too long. Keep it near {t.total_words} words.")
    if not (t.scenes - 1 <= len(script.scenes) <= t.scenes + 2):
        out.append(f"Use exactly {t.scenes} scenes (you wrote {len(script.scenes)}).")
    thin = [sc.id for sc in script.scenes if len(sc.narration.split()) < 0.6 * t.words_per_scene]
    if thin:
        out.append(f"Scenes {', '.join(map(str, thin))} are too short; each scene needs about "
                   f"{t.words_per_scene} words (2-3 full sentences).")
    return out


# --- generation --------------------------------------------------------------------

def _client() -> ollama.Client:
    return ollama.Client(host=settings.ollama_url, timeout=300)


def _options(t: Targets, temperature: float = 0.6) -> dict:
    # leave room for JSON overhead (~25 tokens per scene) on top of ~1.4 tokens per word
    return {"temperature": temperature, "num_ctx": 4096,
            "num_predict": int(t.total_words * 2.5 + t.scenes * 60 + 200)}


def generate_script(req: JobRequest, client: ollama.Client | None = None,
                    log: Log = print, max_attempts: int = 3) -> Script:
    client = client or _client()
    t = Targets.for_duration(req.duration_sec)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(req, t)}]
    best: Script | None = None

    for attempt in range(1, max_attempts + 1):
        log(f"Script attempt {attempt}/{max_attempts} ({settings.ollama_model})")
        resp = client.chat(model=settings.ollama_model, messages=messages,
                           format=Script.model_json_schema(), options=_options(t))
        content = resp.message.content or ""
        try:
            script = tidy(Script.model_validate_json(content), req.topic)
        except ValidationError as e:
            errs = "; ".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:5])
            log(f"  invalid script JSON: {errs}")
            feedback = f"That JSON is invalid: {errs}. Return the full corrected JSON object."
        else:
            if best is None or abs(script.word_count - t.total_words) < abs(best.word_count - t.total_words):
                best = script
            issues = problems(script, t)
            log(f"  {len(script.scenes)} scenes, {script.word_count}/{t.total_words} words")
            if not issues:
                return script
            for issue in issues:
                log(f"  - {issue}")
            feedback = " ".join(issues) + " Return the full corrected JSON object."
        messages += [{"role": "assistant", "content": content}, {"role": "user", "content": feedback}]

    if best is None:
        raise ScriptError(f"Ollama returned no valid script after {max_attempts} attempts")
    return expand_thin_scenes(best, req, t, client, log)


def expand_thin_scenes(script: Script, req: JobRequest, t: Targets,
                       client: ollama.Client, log: Log = print) -> Script:
    """Rewrite scenes that are much shorter than the target, one small call each."""
    scenes = list(script.scenes)
    for i, sc in enumerate(scenes):
        words = len(sc.narration.split())
        if words >= 0.75 * t.words_per_scene:
            continue
        prev = scenes[i - 1].narration if i else "(this is the opening scene, start with a hook)"
        prompt = (
            f"You are writing narration for a short video about: {req.topic}\n"
            f"Tone: {TONE_HINTS[req.tone]}.\n"
            f"Previous scene's narration: {prev}\n"
            f"This scene shows: {sc.visual_description}\n"
            f"Current draft: {sc.narration}\n\n"
            f"Rewrite the draft as {t.words_per_scene} words of spoken narration (2-3 sentences) "
            f"with specific facts. Output only the narration text, nothing else."
        )
        resp = client.generate(model=settings.ollama_model, prompt=prompt,
                               options={"temperature": 0.7, "num_predict": t.words_per_scene * 3})
        new = clean_narration(resp.response or "")
        if len(new.split()) > words:
            log(f"  expanded scene {sc.id}: {words} -> {len(new.split())} words")
            scenes[i] = sc.model_copy(update={"narration": new})
    return script.model_copy(update={"scenes": scenes})


# --- stage entry points ----------------------------------------------------------------

def run(job_dir: Path, req: JobRequest, log: Log = print, client: ollama.Client | None = None) -> Script:
    script = generate_script(req, client=client, log=log)
    (job_dir / SCRIPT_FILE).write_text(script.model_dump_json(indent=2), encoding="utf-8")
    log(f"Script ready: {script.title!r}, {len(script.scenes)} scenes, {script.word_count} words, "
        f"mood={script.music_mood}")
    return script


def load(job_dir: Path) -> Script:
    return Script.model_validate_json((job_dir / SCRIPT_FILE).read_text(encoding="utf-8"))
