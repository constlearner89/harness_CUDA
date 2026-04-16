#!/usr/bin/env bash
set -euo pipefail

INPUT="${*:-}"
if [[ -z "$INPUT" && ! -t 0 ]]; then
  INPUT="$(cat)"
fi

if [[ -z "$INPUT" ]]; then
  exit 0
fi

RM_COMBINED='(^|[[:space:][:punct:]])rm([[:space:]]+--[^[:space:]]+)*([[:space:]]+-[[:alnum:]-]*r[[:alnum:]-]*f[[:alnum:]-]*|[[:space:]]+-[[:alnum:]-]*f[[:alnum:]-]*r[[:alnum:]-]*)([[:space:]]+--[^[:space:]]+)*([[:space:][:punct:]]|$)'
RM_SPLIT_RF='(^|[[:space:][:punct:]])rm([[:space:]]+--[^[:space:]]+)*[[:space:]]+-[[:alnum:]-]*r[[:alnum:]-]*([[:space:]]+--[^[:space:]]+)*[[:space:]]+-[[:alnum:]-]*f[[:alnum:]-]*([[:space:][:punct:]]|$)'
RM_SPLIT_FR='(^|[[:space:][:punct:]])rm([[:space:]]+--[^[:space:]]+)*[[:space:]]+-[[:alnum:]-]*f[[:alnum:]-]*([[:space:]]+--[^[:space:]]+)*[[:space:]]+-[[:alnum:]-]*r[[:alnum:]-]*([[:space:][:punct:]]|$)'
GIT_PUSH_FORCE='(^|[[:space:][:punct:]])git[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+--force(-with-lease)?([[:space:][:punct:]]|$)'
GIT_RESET_HARD='(^|[[:space:][:punct:]])git[[:space:]]+reset([[:space:]]+[^[:space:]]+)*[[:space:]]+--hard([[:space:][:punct:]]|$)'
GIT_CLEAN='(^|[[:space:][:punct:]])git[[:space:]]+clean([[:space:]]+[^[:space:]]+)*[[:space:]]+-[[:alnum:]-]*f[[:alnum:]-]*d[[:alnum:]-]*x?[[:alnum:]-]*([[:space:][:punct:]]|$)'
GIT_CLEAN_REV='(^|[[:space:][:punct:]])git[[:space:]]+clean([[:space:]]+[^[:space:]]+)*[[:space:]]+-[[:alnum:]-]*d[[:alnum:]-]*f[[:alnum:]-]*x?[[:alnum:]-]*([[:space:][:punct:]]|$)'
DROP_TABLE='(^|[[:space:][:punct:]])DROP[[:space:]]+TABLE([[:space:][:punct:]]|$)'

if printf '%s\n' "$INPUT" | grep -Eiq "$RM_COMBINED|$RM_SPLIT_RF|$RM_SPLIT_FR|$GIT_PUSH_FORCE|$GIT_RESET_HARD|$GIT_CLEAN|$GIT_CLEAN_REV|$DROP_TABLE"; then
  echo "BLOCKED: dangerous command pattern detected." >&2
  echo "Input: $INPUT" >&2
  exit 1
fi
