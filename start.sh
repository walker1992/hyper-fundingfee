#!/bin/bash

set -euo pipefail

PID_FILE="quantify.pid"
LOG_DIR="logs"
OUT_LOG="$LOG_DIR/service.out"

mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
  echo "Service already running, PID: $(cat "$PID_FILE")"
  exit 0
fi

nohup python3 -m src.app.runner --config config.json >>"$OUT_LOG" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "服务已启动，PID: $PID"


