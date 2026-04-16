#!/usr/bin/env bash
# ==============================================================================
# MockSQL: repeatable test runner
# Usage:
#   bash scripts/run_tests.sh core
#   bash scripts/run_tests.sh integration
#   bash scripts/run_tests.sh full
# ==============================================================================

set -euo pipefail

MODE="${1:-full}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    . ".venv/bin/activate"
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    else
        echo "ERROR: python or python3 is required."
        exit 1
    fi
fi

case "$MODE" in
    core)
        echo "[MockSQL] Running core tests..."
        "$PYTHON_BIN" -m pytest \
            tests/test_sql_parser.py \
            tests/test_synthesizer.py \
            tests/test_feedback.py \
            -v
        ;;
    integration)
        echo "[MockSQL] Running integration tests..."
        "$PYTHON_BIN" -m pytest tests/test_integration.py -v
        ;;
    full)
        echo "[MockSQL] Running full test suite..."
        "$PYTHON_BIN" -m pytest tests/ -v
        ;;
    *)
        echo "Usage: bash scripts/run_tests.sh [core|integration|full]"
        exit 1
        ;;
esac
