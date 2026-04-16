#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_DIR="$ROOT/.codex/tmp"
STATE_FILE="$STATE_DIR/circuit_breaker.log"
WINDOW_SECONDS="${CIRCUIT_BREAKER_WINDOW:-60}"
THRESHOLD="${CIRCUIT_BREAKER_THRESHOLD:-5}"

mkdir -p "$STATE_DIR"

MESSAGE="${*:-}"
if [[ -z "$MESSAGE" && ! -t 0 ]]; then
  MESSAGE="$(cat)"
fi

if [[ -z "$MESSAGE" ]]; then
  exit 0
fi

SIGNATURE="$(printf '%s' "$MESSAGE" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | cut -c1-200)"
NOW="$(date +%s)"

touch "$STATE_FILE"
tmp_file="$(mktemp)"

awk -F'\t' -v now="$NOW" -v window="$WINDOW_SECONDS" 'NF == 2 && (now - $1) <= window { print }' "$STATE_FILE" > "$tmp_file"
printf '%s\t%s\n' "$NOW" "$SIGNATURE" >> "$tmp_file"
mv "$tmp_file" "$STATE_FILE"

count="$(awk -F'\t' -v sig="$SIGNATURE" '$2 == sig { count++ } END { print count + 0 }' "$STATE_FILE")"

if (( count >= THRESHOLD )); then
  echo "CIRCUIT BREAKER: same error repeated ${count} times within ${WINDOW_SECONDS}s." >&2
  echo "Signature: $SIGNATURE" >&2
  echo "Recommendation: stop retrying blindly and change strategy." >&2
  exit 2
fi
