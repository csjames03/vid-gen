import pytest

from app.models import JobRequest, Script
from app.pipeline import voice as vp
from app.utils import ffmpeg


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory):
    d = tmp_path_factory.mktemp("tts")
    ffmpeg.run(["-f", "lavfi", "-i", "sine=duration=1.2", "-c:a", "libmp3lame", "a.mp3"], cwd=d)
    return (d / "a.mp3").read_bytes()


def fake_communicate(mp3_bytes, fail_times=0):
    state = {"calls": 0}

    class FakeCommunicate:
        def __init__(self, text, voice, rate, boundary):
            assert boundary == "WordBoundary"
            self.text = text

        async def stream(self):
            state["calls"] += 1
            if state["calls"] <= fail_times:
                raise ConnectionError("network down")
            for i, w in enumerate(self.text.split()):
                yield {"type": "WordBoundary", "offset": i * 2_500_000, "duration": 2_000_000, "text": w}
            yield {"type": "audio", "data": mp3_bytes}

    return FakeCommunicate, state


def make_script():
    return Script.model_validate({
        "title": "t", "hook": "h", "music_mood": "calm",
        "scenes": [{"id": i, "narration": "one two three four", "visual_description": "x",
                    "search_queries": ["q"]} for i in (1, 2, 3)],
    })


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(vp.asyncio, "sleep", instant)


def test_run_writes_audio_words_and_manifest(tmp_path, mp3_bytes, monkeypatch):
    fake, _ = fake_communicate(mp3_bytes)
    monkeypatch.setattr(vp.edge_tts, "Communicate", fake)
    m = vp.run(tmp_path, JobRequest(topic="octopus", voice="en-GB-SoniaNeural"), make_script(),
               log=lambda m: None)
    assert [s.scene_id for s in m.scenes] == [1, 2, 3]
    assert m.scenes[0].voice == "en-GB-SoniaNeural"
    assert m.scenes[0].duration == pytest.approx(1.2, abs=0.1)
    assert m.total_duration == pytest.approx(3.6, abs=0.3)
    words = vp.load_words(tmp_path, m.scenes[1])
    assert [w.text for w in words] == ["one", "two", "three", "four"]
    assert (words[1].start, words[1].end) == (0.25, 0.45)
    assert vp.load(tmp_path).scenes[2].file == "audio/scene_03.mp3"


def test_retries_then_succeeds(tmp_path, mp3_bytes, monkeypatch):
    fake, state = fake_communicate(mp3_bytes, fail_times=2)
    monkeypatch.setattr(vp.edge_tts, "Communicate", fake)
    words = vp.asyncio.run(vp.synthesize("hello there", "v", tmp_path / "x.mp3"))
    assert state["calls"] == 3 and len(words) == 2


def test_gives_up_after_retries(tmp_path, mp3_bytes, monkeypatch):
    fake, _ = fake_communicate(mp3_bytes, fail_times=99)
    monkeypatch.setattr(vp.edge_tts, "Communicate", fake)
    with pytest.raises(vp.VoiceError):
        vp.asyncio.run(vp.synthesize("hello", "v", tmp_path / "x.mp3"))
