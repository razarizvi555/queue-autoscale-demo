"""Queue worker that simulates a GPU inference worker.

- On startup it "loads a model" (sleeps MODEL_LOAD_SECONDS). This is the cold-start
  cost that a warm pool avoids.
- It then blocks on the Redis list and processes one job at a time.
- On SIGTERM it finishes the current job before exiting, so scale-down never drops work.
- Prometheus metrics are served on METRICS_PORT.
"""
import json
import os
import signal
import socket
import time

import redis
from prometheus_client import Counter, Gauge, Histogram, start_http_server

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
QUEUE_NAME = os.getenv("QUEUE_NAME", "jobs")
MODEL_LOAD_SECONDS = float(os.getenv("MODEL_LOAD_SECONDS", "20"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))
COST_PER_HOUR = float(os.getenv("WORKER_COST_PER_HOUR", "1.10"))  # pretend GPU price, USD

POD = os.getenv("POD_NAME", socket.gethostname())

JOBS_DONE = Counter("worker_jobs_processed_total", "Jobs completed", ["pod"])
JOBS_FAILED = Counter("worker_jobs_failed_total", "Jobs that raised errors", ["pod"])
JOB_SECONDS = Histogram("worker_job_duration_seconds", "Time spent running a job",
                        buckets=(0.5, 1, 2, 5, 10, 20, 40, 60))
QUEUE_WAIT = Histogram("worker_queue_wait_seconds", "Time a job waited before a worker picked it up",
                       buckets=(0.5, 1, 5, 10, 30, 60, 120, 300))
COLD_START = Gauge("worker_cold_start_seconds", "Model load time for this worker", ["pod"])
BUSY_SECONDS = Counter("worker_busy_seconds_total", "Seconds spent running jobs", ["pod"])
UP_SECONDS = Counter("worker_up_seconds_total", "Seconds this worker has been running", ["pod"])
JOB_COST = Counter("worker_job_cost_usd_total", "Attributed compute cost of completed jobs (USD)", ["pod"])

stopping = False


def handle_sigterm(signum, frame):
    global stopping
    stopping = True
    print(f"[{POD}] SIGTERM received, finishing current job then exiting", flush=True)


def load_model():
    print(f"[{POD}] loading model ({MODEL_LOAD_SECONDS}s cold start)...", flush=True)
    start = time.time()
    time.sleep(MODEL_LOAD_SECONDS)
    elapsed = time.time() - start
    COLD_START.labels(POD).set(elapsed)
    print(f"[{POD}] model loaded in {elapsed:.1f}s", flush=True)


def run_job(job):
    start = time.time()
    QUEUE_WAIT.observe(max(0.0, start - job["enqueued_at"]))
    time.sleep(job.get("work_seconds", 2.0))  # stand-in for inference on a GPU
    elapsed = time.time() - start
    JOB_SECONDS.observe(elapsed)
    BUSY_SECONDS.labels(POD).inc(elapsed)
    JOB_COST.labels(POD).inc(elapsed / 3600 * COST_PER_HOUR)
    JOBS_DONE.labels(POD).inc()


def main():
    signal.signal(signal.SIGTERM, handle_sigterm)
    start_http_server(METRICS_PORT)
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    load_model()
    last_tick = time.time()
    while not stopping:
        item = r.blpop(QUEUE_NAME, timeout=2)
        now = time.time()
        UP_SECONDS.labels(POD).inc(now - last_tick)
        last_tick = now
        if item is None:
            continue
        _, raw = item
        try:
            run_job(json.loads(raw))
        except (ValueError, KeyError, TypeError) as exc:  # bad payload: count it, keep the worker alive
            JOBS_FAILED.labels(POD).inc()
            print(f"[{POD}] job failed: {exc}", flush=True)
    print(f"[{POD}] exited cleanly", flush=True)


if __name__ == "__main__":
    main()
