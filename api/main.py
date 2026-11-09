"""Job intake API.

Accepts jobs over HTTP and pushes them onto a Redis list that workers drain.
Exposes Prometheus metrics at /metrics.
"""
import json
import os
import time
import uuid

import redis
from fastapi import FastAPI, Query
from prometheus_client import Counter, Gauge, make_asgi_app

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
QUEUE_NAME = os.getenv("QUEUE_NAME", "jobs")

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

app = FastAPI(title="queue-autoscale-demo API")
app.mount("/metrics", make_asgi_app())

JOBS_ENQUEUED = Counter("jobs_enqueued_total", "Jobs accepted by the API")
QUEUE_DEPTH = Gauge("queue_depth", "Current number of jobs waiting in the queue")


@app.get("/healthz")
def healthz():
    r.ping()
    return {"status": "ok"}


@app.post("/jobs")
def enqueue(count: int = Query(1, ge=1, le=10_000), work_seconds: float = Query(2.0, gt=0, le=60)):
    """Enqueue `count` jobs, each simulating `work_seconds` of GPU work."""
    now = time.time()
    pipe = r.pipeline()
    for _ in range(count):
        job = {"id": str(uuid.uuid4()), "enqueued_at": now, "work_seconds": work_seconds}
        pipe.rpush(QUEUE_NAME, json.dumps(job))
    pipe.execute()
    JOBS_ENQUEUED.inc(count)
    depth = r.llen(QUEUE_NAME)
    QUEUE_DEPTH.set(depth)
    return {"enqueued": count, "queue_depth": depth}


@app.get("/stats")
def stats():
    depth = r.llen(QUEUE_NAME)
    QUEUE_DEPTH.set(depth)
    return {"queue": QUEUE_NAME, "queue_depth": depth}
