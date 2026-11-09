# Queue-Driven Worker Autoscaling on Kubernetes

A small, runnable model of how GPU inference fleets are operated: jobs land in a queue, workers
scale on **queue depth** (not CPU), every new worker pays a **cold start** to load its model, and
every job carries an **attributed cost**. The point is to measure the trade-off between
scale-to-zero (cheap, slow first response) and a warm pool (fast, pays for idle capacity).

The "GPU work" is simulated with `sleep` so the whole thing runs on a laptop with
[kind](https://kind.sigs.k8s.io/). The manifests note where a real cluster would add
`nvidia.com/gpu` limits and GPU-node tolerations.

## Architecture

```
 client ──POST /jobs──▶ API (FastAPI) ──RPUSH──▶ Redis list "jobs"
                                                     │
                         KEDA polls list length ◀────┤
                         and scales Deployment       │ BLPOP
                                                     ▼
                                  worker pods (0..10): load model once (cold start),
                                  then process jobs; finish in-flight job on SIGTERM
                                                     │
                                    /metrics ──▶ Prometheus ──▶ Grafana
```

| Component | What it does |
|---|---|
| `api/` | FastAPI service that enqueues jobs and reports queue depth |
| `worker/` | Simulated inference worker: cold start, job loop, graceful shutdown, Prometheus metrics |
| `k8s/scaledobject.yaml` | Mode A: scale to zero on an empty queue |
| `k8s/scaledobject-warm.yaml` | Mode B: queue-depth scaling plus a warm floor of 2 workers in business hours |
| `.github/workflows/ci.yml` | Lint, manifest validation, image builds, and an end-to-end kind test that asserts workers scale from zero |

## Run it

Prerequisites: Docker, kind, kubectl, Helm.

```bash
make up          # kind cluster, images, KEDA, Prometheus/Grafana, app (about 5 minutes)
make watch       # in a second terminal: watch worker pods appear and disappear
make burst       # enqueue 200 jobs of 3 seconds each
make stats       # queue depth
make grafana     # Grafana admin password; open http://localhost:3000 (user: admin)
make down        # delete everything
```

Switch autoscaling policy with `make deploy-warm` (warm floor) or `kubectl apply -f k8s/scaledobject.yaml` (scale to zero).

## Useful queries (Prometheus / Grafana)

| Panel | PromQL |
|---|---|
| Queue depth | `queue_depth` |
| Running workers | `count(worker_cold_start_seconds)` |
| Throughput (jobs/s) | `sum(rate(worker_jobs_processed_total[1m]))` |
| p95 queue wait | `histogram_quantile(0.95, sum(rate(worker_queue_wait_seconds_bucket[5m])) by (le))` |
| p95 job duration | `histogram_quantile(0.95, sum(rate(worker_job_duration_seconds_bucket[5m])) by (le))` |
| Worker utilization | `sum(rate(worker_busy_seconds_total[5m])) / sum(rate(worker_up_seconds_total[5m]))` |
| Cost per job (USD) | `sum(increase(worker_job_cost_usd_total[1h])) / sum(increase(worker_jobs_processed_total[1h]))` |

## Results

Fill this in from your own runs. This section is what reviewers read.

| Scenario | p95 queue wait | Time to first job done | Peak workers | Idle worker-minutes | Notes |
|---|---|---|---|---|---|
| Scale to zero, burst of 200 x 3s | | | | | |
| Warm floor of 2, same burst | | | | | |
| listLength 5 vs 20 | | | | | |

## Design decisions

- **Scale on queue depth, not CPU.** GPU workers are often busy waiting on the device, so CPU is a poor signal. Queue length per worker maps directly to latency.
- **Graceful shutdown.** Workers finish the in-flight job on SIGTERM and the pod gets a 90-second grace period, so scale-down never drops work.
- **Cold start is explicit.** `MODEL_LOAD_SECONDS` makes the cost visible. On real GPUs, the fixes are pre-pulled images, cached model weights on node-local disk, and a warm floor during busy hours.
- **Cost attribution per job.** Each worker multiplies its busy time by an hourly rate. Idle time is the difference between up-time and busy-time, which is exactly what a warm pool spends.

## What I would do on a real GPU fleet

- Request `nvidia.com/gpu: 1`, taint GPU node pools, and pin driver and CUDA versions in the node image.
- Pre-pull model images with a DaemonSet and cache weights on local NVMe.
- Route jobs to the cheapest GPU class that meets the latency budget, with a separate queue per class.
- Alert on p95 queue wait and on cold starts above budget, not on CPU.
