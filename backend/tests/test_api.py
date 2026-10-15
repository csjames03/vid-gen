"""API tests with a fake pipeline runner (no Ollama / network needed)."""

import json
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app.jobs import JobStore
from app.main import create_app
from app.models import JobStatus
from app.utils import ffmpeg
from app.worker import Worker

SCRIPT = {
    "title": "Octopus Minds", "hook": "h", "music_mood": "calm",
    "scenes": [{"id": i, "narration": f"scene {i}", "visual_description": "octopus",
                "search_queries": ["octopus"], "media_type": "video"} for i in (1, 2, 3)],
}


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("sample")
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", "v.mp4"], cwd=d)
    return d / "v.mp4"


def make_fake_run(sample):
    def fake_run(job_id, echo=False, store=None):
        st = store.load(job_id)
        d = store.job_dir(job_id)
        st.status = JobStatus.running
        store.save(st)
        if st.request.topic == "please fail":
            st.status, st.error = JobStatus.failed, "RuntimeError: boom"
            store.save(st)
            raise RuntimeError("boom")
        (d / "script.json").write_text(json.dumps(SCRIPT))
        (d / "assets").mkdir(exist_ok=True)
        shutil.copy(sample, d / "assets" / "scene_01.mp4")
        (d / "assets.json").write_text(json.dumps({"scenes": [
            {"scene_id": 1, "file": "assets/scene_01.mp4", "kind": "video", "duration": 2.0},
            {"scene_id": 2}, {"scene_id": 3}]}))
        shutil.copy(sample, d / "final.mp4")
        st.status, st.progress, st.output_url = JobStatus.done, 1.0, f"/jobs/{job_id}/video"
        store.save(st)
    return fake_run


@pytest.fixture
def client(tmp_path, sample):
    store = JobStore(tmp_path / "jobs")
    app = create_app(store=store, worker=Worker(store, run_job=make_fake_run(sample)))
    with TestClient(app) as c:
        yield c


def wait_for(client, job_id, status, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/jobs/{job_id}").json()
        if st["status"] == status:
            return st
        time.sleep(0.05)
    raise AssertionError(f"job never reached {status}: {st}")


def test_health_reports_checks(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert {"ollama", "ffmpeg", "pixabay_key", "music", "caption_font"} <= set(body["checks"])
    assert body["checks"]["ffmpeg"] is True


def test_create_run_and_fetch_results(client):
    r = client.post("/jobs", json={"topic": "octopus minds", "duration_sec": 30})
    assert r.status_code == 201
    job_id = r.json()["id"]
    st = wait_for(client, job_id, "done")
    assert st["title"] == "Octopus Minds" and st["output_url"] == f"/jobs/{job_id}/video"

    [summary] = client.get("/jobs").json()
    assert summary["id"] == job_id and summary["duration"] == pytest.approx(2.0, abs=0.1)
    assert summary["poster_url"] and summary["video_url"]

    v = client.get(f"/jobs/{job_id}/video")
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4"
    part = client.get(f"/jobs/{job_id}/video", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100  # seeking in the browser works
    dl = client.get(f"/jobs/{job_id}/video?download=true")
    assert 'attachment; filename="octopus-minds.mp4"' in dl.headers["content-disposition"]

    assert client.get(f"/jobs/{job_id}/poster").headers["content-type"] == "image/jpeg"
    assert client.get(f"/jobs/{job_id}/script").json()["title"] == "Octopus Minds"

    scenes = client.get(f"/jobs/{job_id}/scenes").json()
    assert [s["id"] for s in scenes] == [1, 2, 3]
    assert scenes[0]["thumbnail_url"] and scenes[1]["thumbnail_url"] is None
    thumb = client.get(scenes[0]["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get(f"/jobs/{job_id}/scenes/2/thumbnail").status_code == 404


def test_events_stream_ends_with_final_state(client):
    job_id = client.post("/jobs", json={"topic": "octopus minds"}).json()["id"]
    wait_for(client, job_id, "done")
    with client.stream("GET", f"/jobs/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [b for b in body.split("\n\n") if b.startswith("event: state")]
    last = json.loads(events[-1].split("data: ", 1)[1])
    assert last["status"] == "done"


def test_failed_job_can_be_retried_from_a_stage(client):
    job_id = client.post("/jobs", json={"topic": "please fail"}).json()["id"]
    st = wait_for(client, job_id, "failed")
    assert "boom" in st["error"]
    assert client.post(f"/jobs/{job_id}/retry?from_stage=nope").status_code == 422
    r = client.post(f"/jobs/{job_id}/retry?from_stage=render")
    assert r.status_code == 200
    st = wait_for(client, job_id, "failed")  # the fake runner fails again
    assert any(line.endswith("Re-queued from render") for line in st["log"])
    assert st["stages"]["render"] == "pending" and st["stages"]["script"] == "pending"


def test_errors_and_delete(client):
    assert client.post("/jobs", json={"topic": "x"}).status_code == 422
    assert client.get("/jobs/20990101-000000-abcdef").status_code == 404
    assert client.get("/jobs/../../etc").status_code == 404
    job_id = client.post("/jobs", json={"topic": "octopus minds"}).json()["id"]
    wait_for(client, job_id, "done")
    assert client.delete(f"/jobs/{job_id}").status_code == 204
    assert client.get(f"/jobs/{job_id}").status_code == 404
