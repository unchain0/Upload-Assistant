# Upload Assistant verification map

This directory is the maintained source for verifying the user-facing CLI of the unchain0 Upload Assistant fork. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Use the checkout that contains `upload.py` and this skill.
- Run `.cursor/skills/verify-upload-assistant/bin/ua-verify launch` so the instance has a disposable `UA_DATA_DIR`.
- The seed config is `data/example_config.py` copied to `$UA_DATA_DIR/data/config.py` before the first process starts.
- Run `ua-verify doctor` and require `doctor ok`, the checkout path, and `config=present`.
- Never drive `~/.local/share/Upload-Assistant` or the checkout `data/` directory.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Treat every command as literal. Keep flags and the `Y` answer unchanged.
- Run actions through `ua-verify drive`.
- One skill directory tracks one instance. Clean up before launching another.
- Restore nothing in the user state directory. Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the command, stdout, stderr, and exit code.
- A usage proof includes both `-h` and `--help`.
- A mutation proof includes a second look at the disposable file that was written.
- Record the feature ID with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with ua-verify` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Show usage](./usage.md) covers short help, full help, and the absence of a web UI.
- [Debug dry-run](./debug-dry-run.md) covers preparing a local file without uploading or seeding the client.
- [Site check](./site-check.md) covers the search-only tracker pass and its log file.
- [Update config](./config-update.md) covers the generator's non-destructive update of an existing disposable config.
