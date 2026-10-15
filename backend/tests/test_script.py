import json
from types import SimpleNamespace

import pytest

from app.models import JobRequest
from app.pipeline import script as sp


def make_script(words_per_scene: int, scenes: int = 5, mood: str = "mysterious") -> dict:
    return {
        "title": '"Octopus Minds"',
        "hook": "Nine brains.",
        "music_mood": mood,
        "scenes": [
            {
                "id": 10 + i,  # wrong ids on purpose, tidy() renumbers
                "narration": "Scene 1: " + " ".join(["word"] * words_per_scene) + " 🐙",
                "visual_description": "Octopus on a reef",
                "search_queries": ["Octopus Intelligence, Underwater!", "octopus intelligence underwater", ""],
            }
            for i in range(scenes)
        ],
    }


class FakeClient:
    """Returns queued chat responses in order; generate() returns a fixed expansion."""

    def __init__(self, chat_payloads, expansion=""):
        self.chat_payloads = list(chat_payloads)
        self.expansion = expansion
        self.chat_calls = []
        self.generate_calls = 0

    def chat(self, model, messages, format, options):
        self.chat_calls.append([m["content"] for m in messages])
        payload = self.chat_payloads.pop(0)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(message=SimpleNamespace(content=content))

    def generate(self, model, prompt, options):
        self.generate_calls += 1
        return SimpleNamespace(response=self.expansion)


REQ = JobRequest(topic="octopuses", duration_sec=30)  # targets: 75 words, 5 scenes, 15/scene


def test_targets():
    assert sp.Targets.for_duration(30) == sp.Targets(75, 5)
    assert sp.Targets.for_duration(60).words_per_scene == 25
    assert sp.Targets.for_duration(90) == sp.Targets(225, 8)


def test_clean_narration_and_query():
    assert sp.clean_narration('Scene 2: "Look **closer** [music swells] 🐙 now."') == "Look closer now."
    assert sp.clean_narration("Narrator - Hello  there") == "Hello there"
    assert sp.clean_query("Octopus Intelligence, Underwater! deep sea") == "octopus intelligence underwater deep"


def test_good_first_attempt_is_tidied():
    client = FakeClient([make_script(words_per_scene=15)])
    s = sp.generate_script(REQ, client=client, log=lambda m: None)
    assert len(client.chat_calls) == 1
    assert [sc.id for sc in s.scenes] == [1, 2, 3, 4, 5]
    assert s.title == "Octopus Minds"
    assert s.scenes[0].narration == " ".join(["word"] * 15)
    assert s.scenes[0].search_queries == ["octopus intelligence underwater"]  # cleaned + deduped


def test_short_script_retries_with_feedback():
    client = FakeClient([make_script(words_per_scene=3), make_script(words_per_scene=15)])
    s = sp.generate_script(REQ, client=client, log=lambda m: None)
    assert len(client.chat_calls) == 2
    feedback = client.chat_calls[1][-1]
    assert "15 words in total" in feedback and "75" in feedback
    assert s.word_count == 75


def test_invalid_json_retries():
    client = FakeClient(["{not json", make_script(words_per_scene=15)])
    s = sp.generate_script(REQ, client=client, log=lambda m: None)
    assert "invalid" in client.chat_calls[1][-1]
    assert len(s.scenes) == 5


def test_still_short_falls_back_to_expanding_scenes():
    expansion = " ".join(["longer"] * 16)
    client = FakeClient([make_script(words_per_scene=3)] * 3, expansion=expansion)
    s = sp.generate_script(REQ, client=client, log=lambda m: None)
    assert client.generate_calls == 5
    assert all(sc.narration == expansion for sc in s.scenes)


def test_all_attempts_invalid_raises():
    client = FakeClient(["nope"] * 3)
    with pytest.raises(sp.ScriptError):
        sp.generate_script(REQ, client=client, log=lambda m: None)


def test_run_writes_script_json(tmp_path):
    client = FakeClient([make_script(words_per_scene=15)])
    sp.run(tmp_path, REQ, log=lambda m: None, client=client)
    assert sp.load(tmp_path).word_count == 75


def test_bad_titles_fall_back_to_topic():
    assert sp.fix_title("Scene 1", "the strangest moons") == "The strangest moons"
    assert sp.fix_title('  ""  ', "octopuses") == "Octopuses"
    assert sp.fix_title("Why Octopuses Are Aliens", "octopuses") == "Why Octopuses Are Aliens"
