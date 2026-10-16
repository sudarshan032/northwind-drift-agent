#!/bin/sh
# Start the full benchmark detached from the terminal; progress in bench/STATUS.json and bench/logs/run.log.
cd "$(dirname "$0")/.." || exit 1
mkdir -p bench/logs
nohup caffeinate -is uv run --group bench python -m bench.run --sizes "${1:-50000,500000,3500000}" \
  > bench/logs/nohup.out 2>&1 &
echo "started pid $!"
