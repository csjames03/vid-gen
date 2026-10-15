import json

import pytest

from app.jobs import JobNotFound, JobStore, new_job_id
from app.models import JobRequest, JobStatus, Stage, StageStatus


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


def test_new_job_creates_folder_and_state(store):
    st = store.create(JobRequest(topic="black holes", aspect="16:9"))
    path = store.job_dir(st.id) / "state.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "queued"
    assert data["request"]["topic"] == "black holes"
    assert data["stages"] == {s.value: "pending" for s in Stage}


def test_save_and_load_roundtrip(store):
    st = store.create(JobRequest(topic="octopuses"))
    st.status = JobStatus.running
    st.stage = Stage.script
    st.stages[Stage.script] = StageStatus.running
    st.add_log("generating script")
    store.save(st)

    again = store.load(st.id)
    assert again.status is JobStatus.running
    assert again.stages[Stage.script] is StageStatus.running
    assert again.log[-1].endswith("generating script")
    assert not (store.job_dir(st.id) / "state.json.tmp").exists()


def test_list_newest_first_and_skips_broken(store):
    a = store.create(JobRequest(topic="first"))
    b = store.create(JobRequest(topic="second"))
    (store.root / "20990101-000000-ffffff").mkdir()  # folder without state.json
    (store.root / "not-a-job").mkdir()
    ids = [j.id for j in store.list()]
    assert set(ids) == {a.id, b.id}
    assert ids == sorted(ids, reverse=True)
    assert len(store.list(limit=1)) == 1


def test_bad_or_unknown_ids_raise(store):
    for bad in ["../../etc", "..", "abc", "20261015-ab12cd"]:
        with pytest.raises(JobNotFound):
            store.load(bad)
    with pytest.raises(JobNotFound):
        store.load(new_job_id())  # valid format, doesn't exist


def test_list_on_missing_root(tmp_path):
    assert JobStore(tmp_path / "nothing").list() == []
