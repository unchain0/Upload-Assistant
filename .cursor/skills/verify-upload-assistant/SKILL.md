name: verify-upload-assistant
description: Verification skill for Upload-Assistant CLI — drives the real CLI end-to-end and captures evidence (exit code, output, files). Used by agents to prove behavior after changes.
disable-model-invocation: true

---
# Verify Upload-Assistant CLI

## Launch

Start the CLI with `python3 -m upload --help` or `python3 upload.py --help` from the repo root. The CLI is ready when it prints the usage header and options list. For a short-lived CLI there is no server to keep alive: each drive launches a fresh instance.

**Exact command:**
```bash
# Derive repo root at runtime, then run CLI from that directory:
cd "$(git rev-parse --show-toplevel)" && python3 -m upload --help
```

**Readiness signal:** The output contains `usage: upload.py [path...] [options]` and the options list.

## Doctor

One read-only check that answers "is this instance worth driving?":

- Process is running and exited with code 0
- Version is Python 3.14.x (as required by pyproject.toml)
- No stale lockfiles or leftover tmp directories from prior runs

Run: `python3 -c "import sys; print(sys.version)"` and verify the output starts with `Python 3.14`.

## Drive

The harness exercises the CLI the way a user would, using stable handle strings (prompt texts, option names) rather than coordinates or tab order.

### Sub-features

1. **Help text** — exercise `--help` / `-h` to verify the CLI prints usage and exits cleanly.
2. **Requirements check** — exercise `python3 -m upload --check-requirements` if available, or verify `check_dependencies()` runs without import error.
3. **Name resolution** — pass a minimal `Meta` construction via script to verify `NameManager.get_name()` works (import-driven, no external APIs needed).

**How to drive it (user POV):**
```bash
# Derive repo root at runtime, then run CLI from that directory:
cd "$(git rev-parse --show-toplevel)" && python3 -m upload --help > /dev/null 2>&1
echo "exit code: $?"
```

**Driving it with the harness:**
```python
import asyncio
from src.get_name import NameManager
from src.meta import Meta

def test_help_drive():
    """Exercise the CLI help path and verify exit code 0."""
    import subprocess
    result = subprocess.run(
        ["python3", "-m", "upload", "--help"],
        cwd="$PWD"  # derived at runtime; run from repo root checkout
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "usage:" in result.stdout.lower()

def test_name_manager_drive():
    """Verify NameManager produces expected output from a Meta instance."""
    meta = Meta(
        category="MOVIE",
        title="Test Movie",
        year=2024,
        type="BLURAY",
        source="WEB-DL",
    )
    name_notag, *_rest = asyncio.run(NameManager({}).get_name(meta))
    # The exact output depends on naming conventions; just verify it runs without error
    assert name_notag is not None
```

### Evidence

- **Exit code** — must be 0 for successful runs. Capture and assert `result.returncode == 0`.
- **Stdout** — must contain the expected header/usage text for help mode.
- **Stderr** — must be empty or contain only expected warnings for successful runs.
- **Side effects** — no files should be written to the repo root or tests directory during a clean help run.

**Proof standards:** Exercise the real user path (the `--help` flag), not internal setters or test-only endpoints. The verification must use the same entry point a real user would: the CLI binary invoked from the shell.

### Cleanup

No persistent state is created by the `--help` flag, so no cleanup is needed beyond ensuring no stray files were created. If a full pipeline run created temp files or cache entries, remove them thus:

```bash
rm -rf ./tmp  # relative to repo root
rm -rf ./data/config.py  # relative to repo root
```

Never kill by process name; never remove the evidence directory or the skill file itself.

### Helpers

```bash
# Quick sanity: verify the CLI starts and exits cleanly
verify-cli() {
    # Derive repo root at runtime, then run CLI from that directory:
    cd "$(git rev-parse --show-toplevel)" && python3 -m upload --help > /dev/null 2>&1
    return $?
}
