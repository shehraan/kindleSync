#!/usr/bin/env bash
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
task_port=${1:-8788}
if ! [[ "$task_port" =~ ^[0-9]+$ ]] || (( task_port < 1024 || task_port > 65535 )); then
    echo 'Backend port must be an integer from 1024 to 65535' >&2
    exit 2
fi
umask 077
mkdir -p "$task_root/data"
cd "$task_root"
# Wait on an existing instance instead of binding twice. The foreground wsl.exe
# task remains alive even if an earlier daemon instance is still serving.
exec flock --exclusive --no-fork "$task_root/data/service.lock" \
    "$task_root/.venv/bin/python" -u -m kobosync --data "$task_root/data" \
    serve --host 0.0.0.0 --port "$task_port" --zone America/Toronto --hour 6 \
    >> "$task_root/data/server.log" 2>&1
