import pytest
from pydantic import ValidationError

from app.models import Aspect, JobRequest, JobState, Script, Stage, StageStatus

SAMPLE_SCRIPT = {
    "title": "Why Octopuses Are Basically Aliens",
    "hook": "Three hearts, blue blood, and a brain in every arm.",
    "music_mood": "mysterious",
    "scenes": [
        {
            "id": i,
            "narration": f"Scene {i} narration about octopuses.",
            "visual_description": "octopus on a coral reef",
            "search_queries": ["octopus reef", "  octopus   close up ", ""],
            "media_type": "video",
        }
        for i in range(1, 6)
    ],
}


def test_script_parses_and_cleans_queries():
    s = Script.model_validate(SAMPLE_SCRIPT)
    assert len(s.scenes) == 5
    assert s.scenes[0].search_queries == ["octopus reef", "octopus close up"]
    assert s.word_count == 5 * 5


def test_script_rejects_bad_mood_and_too_few_scenes():
    with pytest.raises(ValidationError):
        Script.model_validate({**SAMPLE_SCRIPT, "music_mood": "jazzy"})
    with pytest.raises(ValidationError):
        Script.model_validate({**SAMPLE_SCRIPT, "scenes": SAMPLE_SCRIPT["scenes"][:2]})


def test_script_schema_is_ollama_friendly():
    schema = Script.model_json_schema()
    assert schema["type"] == "object"
    assert set(schema["required"]) >= {"title", "hook", "music_mood", "scenes"}


def test_job_request_defaults_and_validation():
    r = JobRequest(topic="  black   holes ")
    assert r.topic == "black holes"
    assert r.aspect is Aspect.portrait and r.aspect.size == (1080, 1920)
    assert Aspect("16:9").size == (1920, 1080)
    with pytest.raises(ValidationError):
        JobRequest(topic="black holes", duration_sec=45)


def test_job_state_roundtrip():
    st = JobState(id="20261015-ab12cd", request=JobRequest(topic="black holes"))
    assert all(v is StageStatus.pending for v in st.stages.values())
    st.add_log("hello")
    again = JobState.model_validate_json(st.model_dump_json())
    assert again.stages[Stage.script] is StageStatus.pending
    assert again.log[0].endswith("hello")
