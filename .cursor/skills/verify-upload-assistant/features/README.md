# Verification Features — Upload-Assistant CLI

The following user-facing features have been mapped for verification. Each file describes the feature from the user's point of view, how to drive it with the harness, and what observable end state proves it works.

## Sub-features

### 1. Help Text

**How to get to it (user POV):**
Run `python3 -m upload --help` or `python3 upload.py --help` from the repo root. The CLI prints the usage header and all available options.

**Driving it with the harness:**
Use the `test_help_drive` harness from `SKILL.md`. It runs the CLI via `subprocess.run()` and asserts:
- Exit code is 0
- stdout contains `usage:`

**Gotchas:**
- If the CLI is missing dependencies, it may fail before printing help. Ensure requirements are installed first.
- The `-h` shorthand works identically to `--help`.

### 2. Name Manager

**How to get to it (user POV):**
The `NameManager` class in `src/get_name.py` generates release names from `Meta` objects. It is driven programmatically via `asyncio.run(NameManager({}).get_name(meta))`.

**Driving it with the harness:**
Use the `test_name_manager_drive` harness from `SKILL.md`. It constructs a `Meta` instance and calls `NameManager.get_name()`, asserting the result is not None.

**Gotchas:**
- The exact output depends on naming conventions and category. The test only verifies the function runs without raising an exception.
- `Meta` must have valid fields for the chosen category; missing required fields may cause errors.

### 3. Requirements Check

**How to get to it (user POV):**
Verify that `check_dependencies()` from `upload.py` imports all required packages without error.

**Driving it with the harness:**
Run `python3 -c "from src.check_requirements import check_dependencies; check_dependencies()"`. This should complete without importing errors or missing-package warnings.

**Gotchas:**
- This does not verify that the packages are correctly versioned, only that they can be imported.
- Some optional dependencies may not be installed; the check should handle that gracefully.

### 4. Full CLI Pipeline (smoke test)

**How to get to it (user POV):**
Run `python3 -m upload /path/to/some/file` with a small test file. This exercises the initial preparation stages.

**Driving it with the harness:**
Create a minimal test file or directory, then run the CLI with `--limit-queue 1` or similar flags to process one item. Capture exit code and any output log lines.

**Gotchas:**
- A full pipeline run may require scene files, metadata APIs, or tracker credentials that are not available in a verification environment. Keep the test scope narrow (e.g., just the prep phase up to meta generation).
- Always clean up temp directories after a pipeline smoke test.