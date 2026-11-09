"""Fast unit tests: API enqueue/stats and worker job accounting, using an in-memory Redis."""
import importlib
import json
import sys
from pathlib import Path

import fakeredis
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "worker"))


def test_enqueue_and_stats():
    main = importlib.import_module("main")
    main.r = fakeredis.FakeRedis(decode_responses=True)
    client = TestClient(main.app)

    resp = client.post("/jobs", params={"count": 5, "work_seconds": 0.01})
    assert resp.status_code == 200
    assert resp.json() == {"enqueued": 5, "queue_depth": 5}

    job = json.loads(main.r.lindex("jobs", 0))
    assert set(job) == {"id", "enqueued_at", "work_seconds"}
    assert client.get("/stats").json()["queue_depth"] == 5


def test_enqueue_rejects_bad_input():
    main = importlib.import_module("main")
    main.r = fakeredis.FakeRedis(decode_responses=True)
    client = TestClient(main.app)
    assert client.post("/jobs", params={"count": 0}).status_code == 422


def test_worker_runs_job_and_attributes_cost():
    worker = importlib.import_module("worker")
    before_done = worker.JOBS_DONE.labels(worker.POD)._value.get()
    before_cost = worker.JOB_COST.labels(worker.POD)._value.get()

    worker.run_job({"id": "t1", "enqueued_at": 0, "work_seconds": 0.05})

    assert worker.JOBS_DONE.labels(worker.POD)._value.get() == before_done + 1
    assert worker.JOB_COST.labels(worker.POD)._value.get() > before_cost
