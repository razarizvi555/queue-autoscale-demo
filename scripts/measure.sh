#!/usr/bin/env bash
# Run one load test and print the numbers for the README Results table.
#
# Usage: ./scripts/measure.sh <label> [jobs] [seconds-per-job]
#   e.g. ./scripts/measure.sh scale-to-zero 200 3
#
# Writes results-<label>.csv (queue depth and ready workers every 2s) and prints:
#   time to first job picked up / done, time to drain, peak workers,
#   p95 queue wait and idle worker-minutes (from Prometheus).
set -euo pipefail

LABEL="${1:?usage: measure.sh <label> [jobs] [seconds-per-job]}"
JOBS="${2:-200}"
SECS="${3:-3}"
NS=queue-demo
API=localhost:8080
PROM_SVC=monitoring-kube-prometheus-prometheus
PROM_PORT=9090

depth() { curl -s "$API/stats" | python3 -c 'import sys, json; print(json.load(sys.stdin)["queue_depth"])'; }
ready() { local n; n=$(kubectl get deploy worker -n "$NS" -o jsonpath='{.status.readyReplicas}'); echo "${n:-0}"; }

if [ "$(depth)" != "0" ]; then
  echo "Queue is not empty. Wait for it to drain before measuring." >&2
  exit 1
fi

echo "[$LABEL] workers ready before burst: $(ready)"
t0=$(date +%s)
curl -s -X POST "$API/jobs?count=${JOBS}&work_seconds=${SECS}" >/dev/null
echo "[$LABEL] enqueued $JOBS jobs of ${SECS}s at $(date -d "@$t0" '+%H:%M:%S')"

out="results-${LABEL}.csv"
echo "t_seconds,queue_depth,ready_workers" > "$out"
first_pick=""; drained=""; peak=0
while true; do
  t=$(( $(date +%s) - t0 ))
  d=$(depth); w=$(ready)
  echo "$t,$d,$w" >> "$out"
  (( w > peak )) && peak=$w
  if [ -z "$first_pick" ] && [ "$d" -lt "$JOBS" ]; then first_pick=$t; fi
  if [ -z "$drained" ] && [ "$d" -eq 0 ]; then drained=$t; fi
  # stop once the queue is empty and the last jobs have had time to finish
  if [ -n "$drained" ] && [ $(( t - drained )) -ge $(( SECS + 5 )) ]; then break; fi
  if [ "$t" -gt 1200 ]; then echo "timed out after 20 minutes" >&2; break; fi
  sleep 2
done
t_end=$(date +%s)

echo "[$LABEL] waiting 60s for Prometheus to scrape final metrics..."
sleep 60

# Query Prometheus through a temporary port-forward.
kubectl port-forward -n monitoring "svc/$PROM_SVC" "$PROM_PORT:9090" >/dev/null 2>&1 &
pf=$!
trap 'kill $pf 2>/dev/null || true' EXIT
sleep 3

window_min=$(( (t_end - t0) / 60 + 2 ))
q() {
  curl -s -G "http://localhost:$PROM_PORT/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c '
import sys, json
r = json.load(sys.stdin).get("data", {}).get("result", [])
v = r[0]["value"][1] if r else "NaN"
print("n/a" if v in ("NaN", "+Inf", "-Inf") else "%.1f" % float(v))'
}
p95_wait=$(q "histogram_quantile(0.95, sum(increase(worker_queue_wait_seconds_bucket[${window_min}m] @ ${t_end})) by (le))")
idle_min=$(q "(sum(increase(worker_up_seconds_total[${window_min}m] @ ${t_end})) - sum(increase(worker_busy_seconds_total[${window_min}m] @ ${t_end}))) / 60")

cat <<EOF

================ $LABEL ================
Time to first job picked up : ${first_pick:-n/a} s
Time to first job done      : $(( ${first_pick:-0} + SECS )) s (approx: pickup + ${SECS}s)
Time to drain queue         : ${drained:-n/a} s
Peak workers                : $peak
p95 queue wait              : ${p95_wait} s
Idle worker-minutes         : ${idle_min}
Raw samples                 : $out
=========================================
EOF
