#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if grep -R -n -E '#include[[:space:]]*[<"](bpf/|linux/bpf\.h)' \
    "$REPO_ROOT/include/perf.h" \
    "$REPO_ROOT/src/perf.cpp"; then
    echo "server performance counters must not require libbpf headers" >&2
    exit 1
fi
