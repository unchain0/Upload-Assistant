#!/usr/bin/env bash
set -euo pipefail

: "${BASE_SHA:?BASE_SHA must point at the pull request base commit}"
: "${HEAD_SHA:?HEAD_SHA must point at the pull request head commit}"
: "${SEMGREP_RULES_DIR:?SEMGREP_RULES_DIR must point at the pinned semgrep-rules checkout}"

REVIEWDOG_REPORTER="${REVIEWDOG_REPORTER:-local}"
REVIEWDOG_FILTER_MODE="${REVIEWDOG_FILTER_MODE:-nofilter}"
REVIEWDOG_FAIL_LEVEL="${REVIEWDOG_FAIL_LEVEL:-error}"

for command_name in reviewdog ruff basedpyright semgrep shellcheck hadolint actionlint uv; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required review tool is missing: %s\n' "$command_name" >&2
        exit 2
    fi
done

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

overall_status=0
internal_failure=0

review_output() {
    local name="$1"
    local format="$2"
    local input_file="$3"
    local level="${4:-error}"

    [[ -s "$input_file" ]] || return 0

    local -a args=(
        "-name=$name"
        "-reporter=$REVIEWDOG_REPORTER"
        "-filter-mode=$REVIEWDOG_FILTER_MODE"
        "-fail-level=$REVIEWDOG_FAIL_LEVEL"
        "-level=$level"
    )
    if [[ "$format" == efm:* ]]; then
        args+=("-efm=${format#efm:}")
    else
        args+=("-f=$format")
    fi

    if ! reviewdog "${args[@]}" < "$input_file"; then
        overall_status=1
    fi
}

check_tool_status() {
    local tool="$1"
    local status="$2"
    local output_file="$3"
    if (( status > 1 )); then
        printf '%s failed to run (exit %d):\n' "$tool" "$status" >&2
        cat "$output_file" >&2 || true
        internal_failure=1
    fi
}

merge_base="$(git merge-base "$BASE_SHA" "$HEAD_SHA")"
mapfile -d '' changed_files < <(git diff --name-only --diff-filter=ACMR -z "$merge_base" "$HEAD_SHA" --)

python_files=()
pyright_files=()
shell_files=()
docker_files=()
workflow_files=()
semgrep_files=()

for file in "${changed_files[@]}"; do
    [[ -f "$file" ]] || continue

    case "$file" in
        *.py)
            python_files+=("$file")
            if [[ "$file" != tests/* && "$file" != data/* && "$file" != tmp/* ]]; then
                pyright_files+=("$file")
            fi
            semgrep_files+=("$file")
            ;;
        *.sh)
            shell_files+=("$file")
            semgrep_files+=("$file")
            ;;
        Dockerfile|Dockerfile.*|*/Dockerfile|*/Dockerfile.*)
            docker_files+=("$file")
            semgrep_files+=("$file")
            ;;
        .github/workflows/*.yml|.github/workflows/*.yaml)
            workflow_files+=("$file")
            semgrep_files+=("$file")
            ;;
        *.yml|*.yaml|*.toml|*.json)
            semgrep_files+=("$file")
            ;;
    esac
done

printf 'Static review changed files: %d\n' "${#changed_files[@]}"

if (( ${#python_files[@]} )); then
    ruff_status=0
    ruff check --force-exclude --output-format=concise "${python_files[@]}" > "$tmp_dir/ruff.out" 2>&1 || ruff_status=$?
    check_tool_status "Ruff" "$ruff_status" "$tmp_dir/ruff.out"
    review_output "ruff" 'efm:%f:%l:%c: %m' "$tmp_dir/ruff.out"

    radon_status=0
    uv run --frozen --no-sync python scripts/check_radon_complexity.py --concise "${python_files[@]}" > "$tmp_dir/radon.out" 2>&1 || radon_status=$?
    check_tool_status "Radon complexity" "$radon_status" "$tmp_dir/radon.out"
    review_output "radon-complexity" 'efm:%f:%l:%c: %m' "$tmp_dir/radon.out"
fi

if (( ${#pyright_files[@]} )); then
    pyright_status=0
    basedpyright --outputjson "${pyright_files[@]}" > "$tmp_dir/basedpyright.json" 2> "$tmp_dir/basedpyright.err" || pyright_status=$?
    if (( pyright_status > 1 )); then
        cat "$tmp_dir/basedpyright.err" >&2 || true
        internal_failure=1
    elif python scripts/pyright_to_rdjson.py < "$tmp_dir/basedpyright.json" > "$tmp_dir/basedpyright.rdjsonl"; then
        review_output "basedpyright" rdjsonl "$tmp_dir/basedpyright.rdjsonl"
    else
        printf 'Failed to convert BasedPyright diagnostics to Reviewdog format.\n' >&2
        internal_failure=1
    fi
fi

if (( ${#shell_files[@]} )); then
    shellcheck_status=0
    shellcheck -f gcc "${shell_files[@]}" > "$tmp_dir/shellcheck.out" 2>&1 || shellcheck_status=$?
    check_tool_status "ShellCheck" "$shellcheck_status" "$tmp_dir/shellcheck.out"
    review_output "shellcheck" 'efm:%f:%l:%c: %m' "$tmp_dir/shellcheck.out"
fi

if (( ${#docker_files[@]} )); then
    hadolint_status=0
    hadolint -f sarif "${docker_files[@]}" > "$tmp_dir/hadolint.sarif" 2> "$tmp_dir/hadolint.err" || hadolint_status=$?
    if (( hadolint_status > 1 )); then
        cat "$tmp_dir/hadolint.err" >&2 || true
        internal_failure=1
    else
        review_output "hadolint" sarif "$tmp_dir/hadolint.sarif"
    fi
fi

if (( ${#workflow_files[@]} )); then
    actionlint_status=0
    actionlint "${workflow_files[@]}" > "$tmp_dir/actionlint.out" 2>&1 || actionlint_status=$?
    check_tool_status "actionlint" "$actionlint_status" "$tmp_dir/actionlint.out"
    review_output "actionlint" 'efm:%f:%l:%c: %m' "$tmp_dir/actionlint.out"
fi

if (( ${#semgrep_files[@]} )); then
    semgrep_status=0
    semgrep scan \
        --disable-version-check \
        --metrics=off \
        --quiet \
        --sarif \
        --config "$SEMGREP_RULES_DIR/python/correctness" \
        --config "$SEMGREP_RULES_DIR/python/lang/correctness" \
        --config "$SEMGREP_RULES_DIR/python/lang/security" \
        --config "$SEMGREP_RULES_DIR/generic/secrets/security" \
        --config "$SEMGREP_RULES_DIR/dockerfile/correctness" \
        --config "$SEMGREP_RULES_DIR/dockerfile/security" \
        "${semgrep_files[@]}" > "$tmp_dir/semgrep.sarif" 2> "$tmp_dir/semgrep.err" || semgrep_status=$?
    if (( semgrep_status > 1 )); then
        cat "$tmp_dir/semgrep.err" >&2 || true
        internal_failure=1
    else
        review_output "semgrep-ce" sarif "$tmp_dir/semgrep.sarif"
    fi
fi

if (( internal_failure )); then
    exit 2
fi
exit "$overall_status"
