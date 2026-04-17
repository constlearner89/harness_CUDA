#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

map_test_file() {
  local file="$1"
  local dir base ext
  dir="$(dirname "$file")"
  base="$(basename "$file")"
  ext="${base##*.}"
  base="${base%.*}"

  if [[ "$base" == test_* ]]; then
    return 1
  fi

  echo "$dir/test_${base}.${ext}"
}

is_test_file() {
  local file="$1"
  [[ "$file" == tests/* ]] && return 0
  [[ "$file" == */test_* || "$file" == test_* ]] && return 0
  [[ "$file" == *_test.* || "$file" == *Test.* ]] && return 0
  return 1
}

requires_test_guard() {
  local file="$1"
  case "$file" in
    *.py|*.c|*.cc|*.cpp|*.cxx|*.cu|*.cuh|*.h|*.hh|*.hpp)
      return 0
      ;;
  esac
  return 1
}

has_related_native_test() {
  local base="$1"

  [[ -d tests ]] || return 1

  find tests -type f \
    \( \
      -name "test_${base}.py" -o \
      -name "test_${base}.c" -o \
      -name "test_${base}.cc" -o \
      -name "test_${base}.cpp" -o \
      -name "test_${base}.cxx" -o \
      -name "test_${base}.cu" -o \
      -name "${base}_test.c" -o \
      -name "${base}_test.cc" -o \
      -name "${base}_test.cpp" -o \
      -name "${base}_test.cxx" -o \
      -name "${base}_test.cu" -o \
      -name "${base}Test.c" -o \
      -name "${base}Test.cc" -o \
      -name "${base}Test.cpp" -o \
      -name "${base}Test.cxx" -o \
      -name "${base}Test.cu" \
    \) | grep -q .
}

if [[ $# -gt 0 ]]; then
  mapfile -t changed_files < <(printf '%s\n' "$@")
else
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    exit 0
  fi
  mapfile -t changed_files < <(
    {
      git diff --name-only HEAD -- '*.py' '*.c' '*.cc' '*.cpp' '*.cxx' '*.cu' '*.cuh' '*.h' '*.hh' '*.hpp' 'tests/**/CMakeLists.txt' 'tests/CMakeLists.txt'
      git ls-files --others --exclude-standard -- '*.py' '*.c' '*.cc' '*.cpp' '*.cxx' '*.cu' '*.cuh' '*.h' '*.hh' '*.hpp' 'tests/**/CMakeLists.txt' 'tests/CMakeLists.txt'
    } | awk 'NF && !seen[$0]++'
  )
fi

if [[ ${#changed_files[@]} -eq 0 ]]; then
  exit 0
fi

changed_set="$(printf '%s\n' "${changed_files[@]}")"
missing=()

for file in "${changed_files[@]}"; do
  requires_test_guard "$file" || continue
  is_test_file "$file" && continue
  [[ "$file" == scripts/hooks/* ]] && continue
  [[ "$file" == raw/* ]] && continue

  base="$(basename "$file")"
  ext="${base##*.}"
  stem="${base%.*}"

  if [[ "$ext" == "py" ]]; then
    test_file="$(map_test_file "$file" || true)"
    [[ -n "${test_file:-}" ]] || continue

    if [[ ! -f "$test_file" ]] && ! printf '%s\n' "$changed_set" | grep -Fxq "$test_file"; then
      missing+=("$file -> $test_file")
    fi
    continue
  fi

  if ! has_related_native_test "$stem" && ! printf '%s\n' "$changed_set" | grep -Eq "^tests/.*(${stem}|test_${stem}|${stem}_test|${stem}Test)"; then
    missing+=("$file -> tests/**/*${stem}*")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "TDD GUARD: missing mapped tests for changed implementation files." >&2
  printf '%s\n' "${missing[@]}" >&2
  exit 1
fi
