#!/usr/bin/env bash
set -euo pipefail
SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT="$(cd "$(dirname "$SCRIPT")/../../../.." && pwd)"
PYTHON="${MINISWE_PYTHON:-$PROJECT/../.venv-mini/bin/python}"
cd "$PROJECT"
exec "$PYTHON" -m evaluation.benchmarks.swe_bench.mini_swe.run "$@"
