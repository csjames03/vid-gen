import pytest

from app.models import Aspect, MusicMood, SceneAudio, VoiceManifest, WordTiming
from app.pipeline import captions as cp
from app.pipeline import music as mp
from app.pipeline import timeline as tl


def voice(*durations):
    return VoiceManifest(scenes=[
        SceneAudio(scene_id=i, file=f"audio/scene_{i:02d}.mp3", words_file="w", duration=d, voice="v")
        for i, d in enumerate(durations, start=1)])


def test_timeline_slots_and_overlap():
    slots = tl.build(voice(5.0, 3.0, 4.0))
    assert [s.start for s in slots] == [0.0, 5.25, 8.5]
    assert [s.clip_length for s in slots] == [5.65, 3.65, 4.25]  # last clip has no overlap
    assert tl.total(slots) == 12.75


def test_attach_punctuation():
    timed = [WordTiming(text=t, start=0, end=0) for t in ["Octopuses", "have", "three", "hearts", "blue", "blood"]]
    assert cp.attach_punctuation(timed, "Octopuses have three hearts, and blue blood!") == \
        ["Octopuses", "have", "three", "hearts,", "blue", "blood!"]


def test_chunking_breaks_on_punctuation_and_length():
    ws = [cp.Word(t, i, i + 0.5) for i, t in enumerate(["Hi,", "octopuses", "are", "extraordinary", "creatures."])]
    assert [[w.text for w in c] for c in cp.chunk(ws)] == \
        [["Hi,"], ["octopuses", "are"], ["extraordinary"], ["creatures."]]


def test_build_ass_highlights_each_word():
    words = [cp.Word("Three", 1.0, 1.3), cp.Word("hearts!", 1.35, 1.8)]
    ass = cp.build_ass([words], [3.0], Aspect.portrait)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
    assert len(events) == 2
    assert events[0].startswith("Dialogue: 0,0:00:01.00,0:00:01.35")
    assert f"{{\\c{cp.HIGHLIGHT}}}THREE" in events[0] and f"{{\\c{cp.HIGHLIGHT}}}HEARTS!" in events[1]
    assert events[1].split(",")[2] == "0:00:02.10"  # held 0.3s after the last word


def test_music_pick_by_mood_with_fallback(tmp_path):
    (tmp_path / "calm").mkdir()
    (tmp_path / "calm" / "a.mp3").write_bytes(b"x")
    (tmp_path / "calm" / "b.mp3").write_bytes(b"x")
    c1 = mp.pick(MusicMood.calm, "job1", tmp_path)
    assert c1.matched_mood and c1.title in {"a", "b"}
    assert mp.pick(MusicMood.calm, "job1", tmp_path).file == c1.file  # same job, same track
    epic = mp.pick(MusicMood.epic, "job1", tmp_path)
    assert epic is not None and not epic.matched_mood
    assert mp.pick(MusicMood.calm, "x", tmp_path / "empty") is None
