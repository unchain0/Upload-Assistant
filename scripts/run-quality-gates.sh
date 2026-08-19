#!/usr/bin/env bash
set -uo pipefail

mkdir -p artifacts
rm -f \
  artifacts/coverage.json \
  artifacts/coverage.xml \
  artifacts/masa-architecture.json \
  artifacts/quality-report.json \
  artifacts/quality-report.md

failures=0
run_gate() {
  local name="$1"
  shift
  printf '\n== %s ==\n' "$name"
  if "$@"; then
    printf '%s: PASS\n' "$name"
  else
    printf '%s: FAIL\n' "$name" >&2
    failures=$((failures + 1))
  fi
}

run_gate "uv lock consistency" uv lock --check
run_gate "Repository policy" uv run python scripts/check_repository_policy.py
run_gate "Python compilation" uv run python -m compileall -q src upload.py config-generator.py scripts
run_gate "Ruff format" uv run ruff format --check .
run_gate "Ruff lint" uv run ruff check .
run_gate "Radon complexity (rank A only)" uv run python scripts/check_radon_complexity.py
run_gate "BasedPyright" uv run basedpyright
run_gate "MASA boundaries" uv run python scripts/check_masa_architecture.py --json artifacts/masa-architecture.json

printf '\n== Python tests and 100%% line coverage ==\n'
if uv run python scripts/run_coverage_shards.py --fail-under 100; then
  printf 'Python tests and coverage: PASS\n'
else
  printf 'Python tests and coverage: FAIL\n' >&2
  failures=$((failures + 1))
fi

coverage_args=()
if [[ -f artifacts/coverage.json ]]; then
  coverage_args=(--coverage-json artifacts/coverage.json)
fi
run_gate "Quality report" uv run python scripts/generate_quality_report.py "${coverage_args[@]}"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" && -f artifacts/quality-report.md ]]; then
  cat artifacts/quality-report.md >>"$GITHUB_STEP_SUMMARY"
fi

if (( failures > 0 )); then
  printf '\nQuality gates failed: %d\n' "$failures" >&2
  exit 1
fi
printf '\nAll quality gates passed.\n'
