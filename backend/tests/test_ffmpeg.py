import pytest

from app.utils import ffmpeg


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    # 2s 720x1280 @30fps test pattern with a 440 Hz tone, plus a 1.5s audio-only file
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                "clip.mp4"], cwd=d)
    ffmpeg.run(["-f", "lavfi", "-i", "sine=duration=1.5", "tone.mp3"], cwd=d)
    return d


def test_find_binaries():
    assert ffmpeg.find_binary("ffmpeg").lower().endswith(("ffmpeg", "ffmpeg.exe"))
    assert ffmpeg.find_binary("ffprobe")


def test_probe_video(media_dir):
    info = ffmpeg.probe(media_dir / "clip.mp4")
    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert (info.width, info.height) == (720, 1280)
    assert info.fps == pytest.approx(30)
    assert info.has_video and info.has_audio and info.is_vertical


def test_probe_duration_audio_with_relative_path(media_dir):
    assert ffmpeg.probe_duration("tone.mp3", cwd=media_dir) == pytest.approx(1.5, abs=0.1)
    assert not ffmpeg.probe("tone.mp3", cwd=media_dir).has_video


def test_errors_are_readable(media_dir):
    with pytest.raises(ffmpeg.FFmpegError) as exc:
        ffmpeg.run(["-i", "does_not_exist.mp4", "out.mp4"], cwd=media_dir)
    assert "does_not_exist.mp4" in exc.value.stderr_tail
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.probe(media_dir / "nope.mp4")


def test_parse_rate():
    assert ffmpeg._parse_rate("30000/1001") == pytest.approx(29.97)
    assert ffmpeg._parse_rate("0/0") is None
