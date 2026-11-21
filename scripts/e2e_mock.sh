#!/usr/bin/env bash
# The gate that must pass before any pull request merges.
#
# The integration doc requires this script to replay the whole journey, photo
# to checkout link, on fixtures with every mock flag on.
#
# Phase 0 status: the contract half is real and runs. The journey replay
# depends on Phase 1 (state, revisions, fit engine, cart and checkout), so it
# is reported as pending rather than faked. The script exits non-zero if
# anything that does exist fails.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  echo "No .venv found. See README.md."
  exit 1
fi

export MOCK_AI=1
export MOCK_COMMERCE=1
export MOCK_ROOM_INPUT=1
export MOCK_AGORA=1

failures=0

step() {
  echo
  echo "=== $1 ==="
}

run() {
  if "$@"; then
    return 0
  fi
  failures=$((failures + 1))
  echo "FAILED: $*"
}

step "1. Contracts and fixtures"
run "$PY" -m pytest tests/contracts -q

step "2. Generated schemas and TypeScript match the models"
run "$PY" scripts/gen_schemas.py --check

step "3. Fit engine stays sandbox-safe"
run "$PY" -m pytest tests/fit -q

step "4. Backend scaffold, Shop MCP allowlist, untrusted text"
run "$PY" -m pytest tests/backend -q

step "5. Full journey replay: photo to checkout link"
echo "PENDING. Implemented in Phase 1 (state and revisions, fit engine, cart,"
echo "checkout audit). This step will replay fixtures/photos/room_good.jpg"
echo "through to a hosted test checkout URL with all four mock flags on."

echo
if [ "$failures" -gt 0 ]; then
  echo "e2e_mock: $failures step(s) failed"
  exit 1
fi
echo "e2e_mock: all implemented steps passed; step 5 is pending Phase 1"
