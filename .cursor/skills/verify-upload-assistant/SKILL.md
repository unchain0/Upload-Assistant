---
name: verify-upload-assistant
description: "Drive the unchain0 Upload Assistant CLI the way a user does, on a disposable UA_DATA_DIR, and capture help, dry-run, and config evidence. Use when proving a CLI change, before calling an upload done, or when a task touches upload.py, config-generator.py, or tracker preparation."
---

# Verify Upload Assistant

Upload Assistant in this checkout is a short-lived CLI. There is no web port, desktop shell, or frontend to drive. A verification run uses one disposable data directory and the `uv` environment already in the checkout.

The checkout root is the directory that contains this skill and `upload.py`. The helper resolves it from its own path. Do not point `UA_DATA_DIR` at `~/.local/share/Upload-Assistant`, `~/.local/share/upload-assistant`, or the checkout `data/` directory. Those are shared with the user.

Two processes can run side by side only when each has its own `UA_DATA_DIR`. This skill stores one instance in `artifacts/run-state.env`. A second `launch` is refused while that directory still exists. Do not start `upload.py` yourself against the user state directory to "finish" a failed run.

## Launch

From the checkout root:

```bash
.cursor/skills/verify-upload-assistant/bin/ua-verify launch
```

`launch` creates `/tmp/ua-verify.<random>` when `UA_DATA_DIR` is unset, copies `data/example_config.py` to `$UA_DATA_DIR/data/config.py` only if that file is absent, records the directory, and runs:

```bash
uv run python upload.py --help
```

Ready means that command exits 0 and its stdout contains `usage: upload.py`. The process then exits. There is no server to wait for. The first start also copies built-in templates into the disposable `data/` tree and may print `[NOTICE] Update available`. That notice is not a failure.

Set `UA_DATA_DIR` yourself only when you already created an empty directory that is not a shared state path. The variable is read when the Python process starts. Changing it later inside the same process does nothing.

## Doctor

```bash
.cursor/skills/verify-upload-assistant/bin/ua-verify doctor
```

Doctor is read-only. It checks that `uv` is on `PATH`, `upload.py` and `data/example_config.py` exist, the recorded data directory is not a shared state path, and `$UA_DATA_DIR/data/config.py` exists. It prints the repo path, the data directory path, and `config=present`. It does not print config values. If the checkout later contains `data/config.py`, doctor warns that startup can copy non-empty settings into the disposable directory.

Run doctor before every drive. If it fails, launch again or clean up. Do not switch to the user state directory.

## Drive

The harness is `ua-verify`. It starts a new `upload.py` process for each action. There is no PTY beyond the config generator's single confirmation line.

```bash
.cursor/skills/verify-upload-assistant/bin/ua-verify drive usage
.cursor/skills/verify-upload-assistant/bin/ua-verify drive debug-dry-run
.cursor/skills/verify-upload-assistant/bin/ua-verify drive site-check
.cursor/skills/verify-upload-assistant/bin/ua-verify drive config-update
```

`drive usage` is the safe path. It runs `upload.py -h` and `upload.py --help` and checks the short and full usage text. `drive debug-dry-run` refuses until `UA_VERIFY_MEDIA` names a real local file and `UA_VERIFY_ALLOW_DRY_RUN=1`, because `--debug` still calls metadata services. `drive site-check` refuses on the example config because that config has no tracker credentials and the command would search live sites. `drive config-update` sends `Y` to the generator's update question and checks that it rewrites only the disposable config.

Read the feature file before driving. A refused drive is an unmet precondition, not a passed feature.

## Evidence

Proof goes to `.cursor/skills/verify-upload-assistant/artifacts/`. Capture the command's stdout, stderr, and exit code. For usage, keep both `usage/short.txt` and `usage/full.txt` plus their `.exit` files. A proof is the action and the resulting text, not a claim that the flag name sounds safe.

`--debug` does not upload to trackers, and `--no-seed` does not add a torrent to the client. Both can still write under `$UA_DATA_DIR/tmp`, call TMDb, and call srrDB. Confirm those effects from the transcript and from files created in the disposable directory. Do not treat the flag names as proof. Do not copy `$UA_DATA_DIR/data/config.py` into the evidence directory.

## Cleanup

```bash
.cursor/skills/verify-upload-assistant/bin/ua-verify cleanup
```

Cleanup deletes the directory recorded in `artifacts/run-state.env` and then deletes that state file. It refuses the user state directories and the checkout `data/` path. It does not delete `artifacts/`. After cleanup, `usage/short.txt`, `usage/full.txt`, and `launch-help.txt` must still be present.

Kill only the process you started. `upload.py` exits by itself. Do not use `killall` or match `python` by name.

## Helpers

`bin/ua-verify` is the only helper. Invoke it by path from the checkout root, as shown above. `launch` writes `artifacts/run-state.env`. `doctor` reads it. `drive` appends transcripts under `artifacts/`. `cleanup` removes the instance only.
