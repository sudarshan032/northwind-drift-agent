#!/bin/sh
# Start the benchmark detached from the terminal; progress in bench/STATUS.json and bench/logs/run.log.
# usage: bench/start.sh [sizes] [extra bench.run options, e.g. --deadline 15:30]
cd "$(dirname "$0")/.." || exit 1
mkdir -p bench/logs
SIZES="${1:-50000,500000,3500000}"
[ $# -gt 0 ] && shift
nohup caffeinate -is uv run --group bench python -m bench.run --sizes "$SIZES" "$@" > bench/logs/nohup.out 2>&1 &
echo "started pid $!"
