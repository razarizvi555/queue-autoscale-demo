#!/usr/bin/env bash
# Usage: ./scripts/burst.sh <jobs> <seconds-per-job>
set -euo pipefail
COUNT="${1:-100}"
SECONDS_PER_JOB="${2:-2}"
curl -s -X POST "localhost:8080/jobs?count=${COUNT}&work_seconds=${SECONDS_PER_JOB}"
echo
