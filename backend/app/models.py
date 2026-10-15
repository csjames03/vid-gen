"""Data contracts shared by the pipeline, the API and (via OpenAPI) the frontend."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# --- enums -------------------------------------------------------------------

class Aspect(StrEnum):
    portrait = "9:16"
    landscape = "16:9"

    @property
    def size(self) -> tuple[int, int]:
        """Output (width, height) in pixels."""
        return (1080, 1920) if self is Aspect.portrait else (1920, 1080)

    @property
    def orientation(self) -> Literal["vertical", "horizontal"]:
        return "vertical" if self is Aspect.portrait else "horizontal"


class Tone(StrEnum):
    fun = "fun"
    informative = "informative"
    dramatic = "dramatic"


class MusicMood(StrEnum):
    upbeat = "upbeat"
    calm = "calm"
    mysterious = "mysterious"
    epic = "epic"
    sad = "sad"
    corporate = "corporate"


class MediaType(StrEnum):
    video = "video"
    image = "image"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    skipped = "skipped"
    failed = "failed"


class Stage(StrEnum):
    script = "script"
    assets = "assets"
    voice = "voice"
    captions = "captions"
    music = "music"
    render = "render"


STAGE_ORDER: list[Stage] = list(Stage)


# --- request -----------------------------------------------------------------

class JobRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    duration_sec: Literal[30, 60, 90] = 60
    aspect: Aspect = Aspect.portrait
    tone: Tone = Tone.informative
    voice: str | None = None  # None -> settings.tts_voice
    captions: bool = True
    music: bool = True

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("topic is too short")
        return v


# --- script (LLM output; its JSON schema is passed to Ollama's `format`) -------

class Scene(BaseModel):
    id: int = Field(ge=1)
    narration: str = Field(min_length=1, description="What the narrator says in this scene.")
    visual_description: str = Field(description="What should be on screen.")
    search_queries: list[str] = Field(
        min_length=1,
        max_length=3,
        description="1-3 short, concrete stock-footage search terms, most specific first.",
    )
    media_type: MediaType = MediaType.video

    @field_validator("search_queries")
    @classmethod
    def _clean_queries(cls, v: list[str]) -> list[str]:
        cleaned = [" ".join(q.split()) for q in v if q and q.strip()]
        if not cleaned:
            raise ValueError("search_queries must contain at least one non-empty query")
        return cleaned


class Script(BaseModel):
    title: str = Field(min_length=1)
    hook: str = Field(description="One punchy opening line.")
    music_mood: MusicMood
    scenes: list[Scene] = Field(min_length=3, max_length=10)

    @property
    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)


# --- assets (assets.json, written by the assets stage) ----------------------------

class SceneAsset(BaseModel):
    scene_id: int
    file: str | None = None  # relative to the job folder, e.g. "assets/scene_01.mp4"; None = missing
    kind: MediaType | None = None
    source: str | None = None  # "pixabay", "openverse", "title_card", ...
    source_id: str | None = None
    query: str | None = None  # the search query that found it
    width: int | None = None
    height: int | None = None
    duration: float | None = None  # seconds, videos only
    est_duration: float = 0.0  # narration length estimated from the word count
    page_url: str | None = None  # for credits
    author: str | None = None
    thumbnail: str | None = None


class AssetManifest(BaseModel):
    scenes: list[SceneAsset]

    @property
    def missing(self) -> list[int]:
        return [a.scene_id for a in self.scenes if a.file is None]


# --- voice (voice.json + audio/scene_XX.words.json, written by the voice stage) -----

class WordTiming(BaseModel):
    text: str
    start: float  # seconds from the start of the scene's audio
    end: float


class SceneAudio(BaseModel):
    scene_id: int
    file: str  # relative to the job folder, e.g. "audio/scene_01.mp3"
    words_file: str
    duration: float  # measured with ffprobe
    voice: str


class VoiceManifest(BaseModel):
    scenes: list[SceneAudio]

    @property
    def total_duration(self) -> float:
        return round(sum(s.duration for s in self.scenes), 2)


# --- music (music.json, written by the music stage) ------------------------------

class MusicChoice(BaseModel):
    file: str  # absolute path to the track in MUSIC_DIR
    mood: MusicMood
    matched_mood: bool  # False = no track for the mood, picked from another folder
    title: str


# --- job state (state.json, GET /jobs/{id}) -------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobState(BaseModel):
    id: str
    request: JobRequest
    status: JobStatus = JobStatus.queued
    stage: Stage | None = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    stages: dict[Stage, StageStatus] = Field(
        default_factory=lambda: {s: StageStatus.pending for s in STAGE_ORDER}
    )
    log: list[str] = Field(default_factory=list)
    error: str | None = None
    output_url: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def add_log(self, msg: str) -> None:
        self.log.append(f"{datetime.now():%H:%M:%S} {msg}")  # local time, for humans
        self.updated_at = _now()
