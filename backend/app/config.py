"""App settings, loaded from the repo-root .env file (see ../.env.example)."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # stock media providers
    pixabay_api_key: str = ""
    pexels_api_key: str = ""  # optional; Pexels key issuance is paused

    # local LLM
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"

    # voice
    tts_voice: str = "en-US-AndrewNeural"
    tts_rate: str = "+0%"  # edge-tts speaking rate, e.g. "+10%" for snappier shorts

    # api
    api_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    # paths (relative paths in .env resolve against the repo root)
    music_dir: Path = BACKEND_DIR / "assets" / "music"
    data_dir: Path = REPO_ROOT / "data"
    fonts_dir: Path = BACKEND_DIR / "assets" / "fonts"

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        # allow a comma-separated string in .env
        if isinstance(v, str) and not v.startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("music_dir", "data_dir", "fonts_dir", mode="after")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


settings = Settings()
