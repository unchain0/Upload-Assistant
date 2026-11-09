# Show usage

Show usage lets a person read how to start an upload from the terminal, either as a short reminder or as the full option list, without starting an upload.

## Sub-features

- `usage-short` prints the common options for `-h`.
- `usage-full` prints every option for `--help`.
- `usage-cli-only` stays a terminal help text and does not open a port.

## How to get to it (user POV)

- Run `uv run python upload.py -h` in the checkout.
- Run `uv run python upload.py --help` in the checkout.

## Driving it with ua-verify

Preconditions:

- `ua-verify doctor` prints `doctor ok` for this run's disposable data directory.
- No upload path is required.

- **Short help.** Ask for the reminder. Run `.cursor/skills/verify-upload-assistant/bin/ua-verify drive usage`. The short transcript exits 0, contains `usage: upload.py`, and contains `Common options:`.
- **Full help.** The same drive runs the long form. `artifacts/usage/full.txt` exits 0 and contains `--debug` and `--no-seed`.
- **No web UI.** Read both transcripts. Neither contains `web_ui`, `0.0.0.0`, or `127.0.0.1:` followed by a port.
- **Proof.** Keep `artifacts/usage/short.txt`, `artifacts/usage/full.txt`, and both `.exit` files. Each exit file is `0`.

## Gotchas

- `-h` and `--help` are different texts. A passing full help does not prove the short help.
- The first launch can print `Restored` and `[NOTICE] Update available` on stdout. Those lines are not a second interface.
- Help still loads config. Without `UA_DATA_DIR`, the process uses the user state directory.
- An update notice means GitHub was contacted. It does not mean an upload ran.
