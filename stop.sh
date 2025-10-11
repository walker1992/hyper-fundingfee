#!/bin/bash

set -euo pipefail

PID_FILE="quantify.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "No PID file found. Nothing to stop."
  exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" >/dev/null 2>&1; then
  echo "Sending SIGTERM to process $PID for graceful shutdown"
  kill -TERM "$PID" || true
  # give it time to execute shutdown gracefully
  sleep 5
  if kill -0 "$PID" >/dev/null 2>&1; then
    echo "Process $PID still running after SIGTERM, sending SIGKILL"
    kill -9 "$PID" || true
  fi
  echo "Stopped service (PID: $PID)"
else
  echo "Process $PID not running"
fi

rm -f "$PID_FILE"


