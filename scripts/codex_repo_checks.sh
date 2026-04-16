#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ -f AGENTS.md ]] || { echo "Missing required rule file: AGENTS.md" >&2; exit 1; }
for doc in docs/PRD.md docs/ARCHITECTURE.md docs/ADR.md docs/RESULTS_POLICY.md; do
  [[ -f "$doc" ]] || { echo "Missing required core doc: $doc" >&2; exit 1; }
done

./scripts/hooks/tdd-guard.sh
bash -n scripts/codex_run.sh scripts/codex_repo_checks.sh scripts/hooks/*.sh
python3 -m py_compile scripts/execute.py scripts/test_execute.py scripts/test_hooks.py
python3 -m pytest -q scripts
