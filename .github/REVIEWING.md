# Deterministic pull request review

Automated PR review is performed by `.github/workflows/deterministic-review.yml`.
The repository-level CodeRabbit and Qodo/PR-Agent apps may remain installed, but
their automatic reviews are disabled by `.coderabbit.yaml` and `.pr_agent.toml`.

## Static review

Reviewdog publishes diagnostics only for added lines in the pull request. The
workflow runs these pinned analyzers:

- Ruff 0.16.3 for Python lint and security-oriented rules already configured in `pyproject.toml`.
- Pyright 1.1.413 for changed production Python files.
- ESLint from `web_ui/static/js/package-lock.json` for changed Web UI JavaScript.
- ShellCheck 0.11.0 for changed shell scripts.
- Hadolint 2.15.1 for changed Dockerfiles.
- actionlint 1.7.12 for changed GitHub Actions workflows.
- Semgrep CE 1.173.0 using rules pinned to semgrep-rules commit `40b8c63f75dc7c22c8a77482d73bfb864b146f7e`.

Reviewdog itself is pinned to v0.21.0. ShellCheck and Hadolint downloads are
SHA-256 verified before use. Go-installed tools and npm/pip dependencies use
explicit versions.

For pull requests from the same repository, Reviewdog publishes an inline PR
review. GitHub downgrades the workflow token for fork pull requests, so those
runs use Reviewdog's local reporter instead of requesting write permissions on
untrusted code.

## PR policy review

Danger JS 13.0.10 checks PR-level rules that source linters cannot express. Internal-branch PRs receive the normal Danger comment; fork PRs run the same policy in `--text-only` mode so GitHub's read-only fork token still enforces the required check without granting write access to untrusted code:

- a meaningful PR description is required;
- committed `.env` files are rejected;
- very large PRs are warned about;
- production Python changes without test changes are warned about;
- dependency, workflow, and Dockerfile changes receive explicit review notes.

`package.json` overrides Danger's transitive `undici` dependency to 6.28.0 so
`npm audit --audit-level=moderate` remains clean.

## Local reproduction

The workflow is intentionally composed from normal CLI tools. After installing
the pinned tool versions, set `BASE_SHA`, `HEAD_SHA`, and `SEMGREP_RULES_DIR`
and run:

```bash
REVIEWDOG_REPORTER=local REVIEWDOG_FILTER_MODE=nofilter scripts/run-static-review.sh
```

The existing test and build workflows remain separate and continue to validate
Python 3.14, pytest on Ubuntu/Windows, and multi-architecture Docker builds.
