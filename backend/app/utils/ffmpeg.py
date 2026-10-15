"""Thin wrappers around the ffmpeg / ffprobe CLIs.

Every pipeline stage that touches media goes through `run()` so errors look the same
everywhere. Pass `cwd=job_dir` and relative paths; this avoids Windows path escaping
problems inside filter graphs (e.g. `subtitles=C\\:/...`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

StrPath = str | Path


class FFmpegError(RuntimeError):
    """ffmpeg/ffprobe exited non-zero. `stderr_tail` holds the useful last lines."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
        self.stderr_tail = "\n".join(lines[-15:])
        super().__init__(f"{Path(cmd[0]).stem} failed (exit {returncode}):\n{self.stderr_tail}")


@cache
def find_binary(name: str) -> str:
    """Locate ffmpeg/ffprobe. Falls back to the winget install folder, because a
    process started before installing ffmpeg won't have the updated PATH."""
    if found := shutil.which(name):
        return found
    winget_pkgs = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    for exe in winget_pkgs.glob(f"Gyan.FFmpeg*/*/bin/{name}.exe"):
        return str(exe)
    raise FileNotFoundError(f"{name} not found. Install it: winget install Gyan.FFmpeg")


def run(args: list[StrPath], cwd: StrPath | None = None, timeout: float | None = None) -> str:
    """Run ffmpeg with `args` (everything after the binary). Overwrites outputs.
    Returns stderr (ffmpeg's log) on success, raises FFmpegError on failure."""
    cmd = [find_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           *map(str, args)]
    return _exec(cmd, cwd, timeout).stderr


@dataclass(frozen=True)
class MediaInfo:
    duration: float  # seconds
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_video: bool = False
    has_audio: bool = False

    @property
    def is_vertical(self) -> bool:
        return bool(self.width and self.height and self.height > self.width)


def probe(path: StrPath, cwd: StrPath | None = None) -> MediaInfo:
    """Read duration, size, fps and stream types of a media file."""
    cmd = [find_binary("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    data = json.loads(_exec(cmd, cwd, timeout=60).stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _to_float(data.get("format", {}).get("duration"))
    if duration is None:  # some containers only report it per stream
        duration = max((_to_float(s.get("duration")) or 0.0 for s in streams), default=0.0)

    return MediaInfo(
        duration=duration or 0.0,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=_parse_rate(video.get("avg_frame_rate")) if video else None,
        has_video=video is not None,
        has_audio=audio is not None,
    )


def probe_duration(path: StrPath, cwd: StrPath | None = None) -> float:
    return probe(path, cwd).duration


# --- internals -------------------------------------------------------------------

def _exec(cmd: list[str], cwd: StrPath | None, timeout: float | None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(cmd, -1, f"timed out after {timeout}s\n{e.stderr or ''}") from e
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr)
    return proc


def _to_float(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_rate(rate: str | None) -> float | None:
    """'30000/1001' -> 29.97"""
    if not rate or rate == "0/0":
        return None
    num, _, den = rate.partition("/")
    try:
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return None
