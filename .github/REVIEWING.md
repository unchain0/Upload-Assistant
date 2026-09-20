# Deterministic pull request review

The repository uses two reproducible, non-LLM quality paths.

## Required quality gate

`.github/workflows/masa-quality-gate.yml` runs for every pull request and every
push to `development`. It synchronizes the locked `uv` environment and runs:

- Python compilation;
- Ruff;
- Radon cyclomatic complexity with a hard rank-A-only threshold (CC <= 5 per block);
- BasedPyright;
- the deterministic MASA import-boundary audit;
- the complete pytest suite with a hard 100% line-coverage threshold;
- a machine-readable codebase metrics report.

The generated architecture, coverage, and metrics files are uploaded as workflow
artifacts even when a gate fails.

## Inline static review

`.github/workflows/deterministic-review.yml` runs stack-native analyzers and
publishes added-line diagnostics through Reviewdog:

- Ruff, Radon rank-A complexity, and BasedPyright for Python;
- Semgrep Community Edition with a pinned rules checkout;
- ShellCheck for shell scripts;
- Hadolint for Dockerfiles;
- actionlint for GitHub Actions.

All third-party Actions are pinned to immutable commit SHAs. The workflow uses
`pull_request`, minimum permissions, the repository `GITHUB_TOKEN`, and a local
reporter for fork pull requests. No LLM reviewer or automatic remote fix is
enabled.

## Local reproduction

Synchronize the environment, enable the repository commit hook, and run the full required gate:

```bash
uv sync --frozen --all-groups --no-install-project
scripts/install-git-hooks.sh
scripts/run-quality-gates.sh
```

The committed `.githooks/pre-commit` hook runs the same Radon rank-A complexity check before every local commit. The threshold and analyzed roots live in `pyproject.toml`.

For diff-oriented static review, install the pinned tools used by the workflow,
set `BASE_SHA`, `HEAD_SHA`, and `SEMGREP_RULES_DIR`, then run:

```bash
REVIEWDOG_REPORTER=local REVIEWDOG_FILTER_MODE=nofilter scripts/run-static-review.sh
```
