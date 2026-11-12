# RFC-001: Scheduled Warm Pools for Queue-Driven Workers

| | |
|---|---|
| **Author** | Raza Rizvi |
| **Status** | Proposed |
| **Created** | 2026-11-11 |
| **Applies to** | Queue-driven worker fleets on Kubernetes scaled by KEDA |
| **Example service** | A public, interactive product demo (for example, a sandbox banking dashboard like `demo.mercury.com`) whose backend renders demo data and jobs on demand |

## 1. Summary

Run queue-driven workers with a **small warm floor during business hours and scale to zero overnight**,
instead of scaling to zero all the time or keeping a fixed fleet. In load tests on this repo, a warm
floor of 2 workers cut time to first result from **25 s to 3 s** and p95 queue wait from **94 s to
80 s**, for a predictable idle cost. Scheduling the floor removes most of that cost outside the hours
when people actually arrive.

## 2. Motivation

A public product demo is a good example of the problem. Visitors arrive in bursts (after a launch,
a newsletter, or a sales call), almost always during business hours, and they judge the product by
the first few seconds. Two common setups both fail them:

- **Scale to zero all day:** cheapest, but the first visitor in each burst waits for a cold start.
  In our tests that was about 22 s before any work began.
- **Fixed fleet sized for peak:** fast, but pays for idle capacity around the clock, including nights
  and weekends with almost no traffic.

We want fast first responses when users are likely to show up, near-zero cost when they are not, and
a change that is small, reversible, and measurable.

> **Assumption:** the example demo backend uses a queue (jobs per page load or per session) and
> workers with a meaningful startup cost (loading data fixtures, models, or caches). If a service has
> no startup cost, this RFC does not apply; plain autoscaling is enough.

## 3. Evidence

Tests used this repository: a kind cluster, KEDA scaling a worker Deployment on Redis list length,
max 10 workers, a simulated 20 s cold start, and a burst of 200 jobs of 3 s each. Raw samples are in
`results-*.csv`; p95 queue wait is computed from those samples because the Prometheus histogram
overestimated it (see the README).

| Configuration | Time to first job done | p95 queue wait | Time to drain | Idle worker-minutes (test window) |
|---|---|---|---|---|
| Scale to zero, listLength 5 | 25 s | 94 s | 98 s | 1.1 |
| Warm floor of 2, listLength 5 | **3 s** | **80 s** | **84 s** | 3.1 |
| Scale to zero, listLength 20 | 26 s | 95 s | 97 s | 0.1 |

What the data says:

1. **A warm floor fixes the first-response problem.** The first job finished about 8x faster.
2. **A floor of 2 only partly helps a large burst.** The other 8 workers still paid cold starts, so
   the whole burst finished just 14 s sooner. A floor is for *first impressions*, not throughput.
3. **The cost is predictable.** Two always-on workers cost about 120 idle worker-minutes per quiet
   hour. Limiting the floor to 12 hours on weekdays (60 of 168 hours a week) cuts that idle cost by about 64%.
4. **listLength tunes release timing, not burst latency, at this scale.** Both values hit the
   10-worker cap, so latency matched; listLength 20 released workers earlier and cut idle time.

## 4. Goals and non-goals

**Goals**
- First result in under 5 s for the first request of a burst during business hours.
- No idle workers overnight and on weekends unless traffic arrives.
- One configuration change per service, reviewed in Git and reversible in a single revert.

**Non-goals**
- Predictive or ML-based scaling. Schedules are simpler and cover the main pattern.
- Changing the maximum fleet size or queue technology.
- Removing cold starts entirely; that needs image and cache work (see §8).

## 5. Proposal

Add a KEDA **cron trigger** next to the existing queue trigger on each eligible ScaledObject. KEDA
takes the higher of the two, so the queue still scales the fleet up during bursts, while the cron
trigger holds a minimum during business hours.

```yaml
spec:
  minReplicaCount: 0
  maxReplicaCount: 10
  triggers:
    - type: redis                 # unchanged: scale on queue depth
      metadata:
        listName: jobs
        listLength: "20"          # release idle workers sooner (see §3, finding 4)
    - type: cron                  # new: warm floor during expected traffic
      metadata:
        timezone: America/New_York
        start: 0 8 * * 1-5        # 8am weekdays
        end: 0 20 * * 1-5         # 8pm weekdays
        desiredReplicas: "2"
```

Defaults:
- **Floor size:** 2. That's enough to serve the first visitors instantly; larger bursts still scale out.
- **Window:** 8am to 8pm on weekdays in the main user time zone, set per service from its own traffic data.
- **listLength:** 20 for latency-tolerant background work; keep 5 where every queued job is a waiting user.

## 6. Alternatives considered

| Option | Why not |
|---|---|
| Always-on minimum (`minReplicaCount: 2`) | Same latency win, but pays for idle workers 24/7: 168 floor-hours a week instead of 60, nearly 3x the idle cost of a weekday business-hours schedule |
| Larger floor sized for peak bursts | Pays for peak capacity all day to save seconds on rare large bursts |
| Faster cold starts only (image pre-pulls, local caches) | Worth doing, and complementary (§8), but it shortens cold starts rather than removing them |
| Predictive autoscaling | More moving parts and harder to reason about; revisit if schedules prove too coarse |

## 7. Risks and mitigations

- **Traffic outside the window still sees cold starts.** Mitigation: the queue trigger still scales
  from zero; track off-hours cold starts and widen the window if they matter.
- **Time zone and daylight saving mistakes.** Mitigation: use IANA zone names in the trigger, never
  fixed UTC offsets.
- **KEDA behavior changes with a floor above zero.** With a minimum above 0, the HPA handles all
  scaling and `pollingInterval` and `cooldownPeriod` stop applying (KEDA warns about this). Review
  HPA scale-down settings before rollout.
- **Silent cost growth if floors spread to every service.** Mitigation: floors only for
  user-facing paths with a measured cold-start cost, and idle worker-minutes tracked per service.

## 8. Rollout plan

1. Apply to one non-critical service, then compare a week of metrics against the previous week.
2. Success criteria: time to first result under 5 s in-window, and idle worker-minutes within the
   estimate in §3.
3. Roll out to other eligible services through pull requests, one service per change.
4. Follow-up work, tracked separately: pre-pull worker images on nodes and cache startup data
   locally to shorten cold starts outside the window.

## 9. Metrics

| Metric | Source |
|---|---|
| Time to first result per burst | Queue wait histogram (with finer buckets) |
| p95 queue wait | `histogram_quantile` over `worker_queue_wait_seconds` |
| Idle worker-minutes per day | `worker_up_seconds_total` minus `worker_busy_seconds_total` |
| Cold starts outside the window | `worker_cold_start_seconds` series created between 8pm and 8am |

## 10. Open questions

- Should the window come from each service's actual traffic histogram instead of a fixed default?
- Do weekends need a smaller floor of 1 rather than zero for user-facing demos?
- Should the histogram buckets (60 s to 120 s gap) be fixed before rollout so p95 reporting is trustworthy?
