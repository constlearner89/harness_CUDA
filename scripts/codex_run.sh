#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_CHECKS=0

if [[ "${1:-}" == "--run-checks" ]]; then
  RUN_CHECKS=1
  shift
fi

COMMAND_STRING=""
if [[ $# -gt 0 ]]; then
  case "$1" in
    exec|e|review)
      if [[ $# -gt 1 ]]; then
        COMMAND_STRING="$(printf '%s ' "${@:2}")"
      fi
      ;;
    *)
      COMMAND_STRING="$(printf '%s ' "$@")"
      ;;
  esac
fi

if [[ -n "$COMMAND_STRING" ]]; then
  "$ROOT/scripts/hooks/dangerous-cmd-guard.sh" "$COMMAND_STRING"
fi

set +e
if [[ $# -eq 0 ]]; then
  codex -C "$ROOT"
  exit_code=$?
else
  codex -C "$ROOT" "$@"
  exit_code=$?
fi
set -e

if (( exit_code != 0 )); then
  "$ROOT/scripts/hooks/circuit-breaker.sh" "codex_run exit code ${exit_code}: $* " || true
fi

if [[ $RUN_CHECKS -eq 1 ]]; then
  set +e
  "$ROOT/scripts/codex_repo_checks.sh"
  checks_exit_code=$?
  set -e
  if (( checks_exit_code != 0 )); then
    echo "WARN: repo checks failed after codex run (exit ${checks_exit_code})." >&2
    if (( exit_code == 0 )); then
      exit "$checks_exit_code"
    fi
  fi
fi

exit "$exit_code"
