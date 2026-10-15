"""End-to-end render on tiny synthetic inputs (small frame size so it runs in seconds)."""

import json

import pytest

from app.models import AssetManifest, JobRequest, MediaType, SceneAsset, SceneAudio, VoiceManifest, WordTiming
from app.pipeline import captions, render, timeline
from app.utils import ffmpeg

SIZE = (180, 320)


@pytest.fixture
def job(tmp_path):
    run = lambda *a: ffmpeg.run(list(a), cwd=tmp_path)  # noqa: E731
    (tmp_path / "assets").mkdir()
    (tmp_path / "audio").mkdir()
    run("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3", "-c:v", "libx264",
        "-preset", "ultrafast", "assets/scene_01.mp4")  # long enough
    run("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=0.5", "-c:v", "libx264",
        "-preset", "ultrafast", "assets/scene_02.mp4")  # too short: loops
    run("-f", "lavfi", "-i", "testsrc2=size=400x600", "-frames:v", "1", "assets/scene_03.jpg")
    durations = [1.2, 1.0, 0.8, 0.6]
    for i, d in enumerate(durations, start=1):
        run("-f", "lavfi", "-i", f"sine=frequency={300 + 100 * i}:duration={d}", f"audio/scene_{i:02d}.mp3")
        (tmp_path / f"audio/scene_{i:02d}.words.json").write_text(
            json.dumps([{"text": "hello", "start": 0.1, "end": 0.4}, {"text": "world", "start": 0.45, "end": d - 0.1}]))
    assets = AssetManifest(scenes=[
        SceneAsset(scene_id=1, file="assets/scene_01.mp4", kind=MediaType.video, duration=3.0),
        SceneAsset(scene_id=2, file="assets/scene_02.mp4", kind=MediaType.video, duration=0.5),
        SceneAsset(scene_id=3, file="assets/scene_03.jpg", kind=MediaType.image),
        SceneAsset(scene_id=4),  # missing -> gradient placeholder
    ])
    voice = VoiceManifest(scenes=[
        SceneAudio(scene_id=i, file=f"audio/scene_{i:02d}.mp3", words_file=f"audio/scene_{i:02d}.words.json",
                   duration=ffmpeg.probe_duration(f"audio/scene_{i:02d}.mp3", cwd=tmp_path), voice="v")
        for i in range(1, 5)])
    return tmp_path, assets, voice


def test_render_end_to_end_with_captions_no_music(job):
    job_dir, assets, voice = job
    req = JobRequest(topic="test video", music=False)
    words = [[captions.Word("hello", s.start + 0.1, s.start + 0.4)] for s in timeline.build(voice)]
    (job_dir / "captions.ass").write_text(
        captions.build_ass(words, [s.start + s.length for s in timeline.build(voice)], req.aspect))

    out = render.run(job_dir, req, assets, voice, log=lambda m: None, size=SIZE)

    slots = timeline.build(voice)
    for s in slots:  # each scene clip has exactly its clip length
        info = ffmpeg.probe(f"scenes/scene_{s.scene_id:02d}.mp4", cwd=job_dir)
        assert (info.width, info.height) == SIZE
        assert info.duration == pytest.approx(s.clip_length, abs=0.05)
    final = ffmpeg.probe(out)
    assert final.has_video and final.has_audio
    assert (final.width, final.height) == SIZE
    assert final.duration == pytest.approx(timeline.total(slots), abs=0.1)


def test_final_graph_mentions_music_and_ducking():
    voice = VoiceManifest(scenes=[SceneAudio(scene_id=i, file="a", words_file="w", duration=2, voice="v")
                                  for i in (1, 2)])
    g = render.final_graph(["a", "b"], voice, timeline.build(voice), has_music=True, has_captions=False)
    assert "xfade" in g and "[4:a]" in g and "sidechaincompress" in g and "subtitles" not in g
