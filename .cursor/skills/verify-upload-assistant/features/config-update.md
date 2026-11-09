# Update config

Update config lets a person add new options from the example file to an existing config without answering every prompt again.

## Sub-features

- `config-open` starts the generator against the runtime config.
- `config-accept-update` accepts the update question and writes the file.
- `config-keep-values` leaves existing values in place while adding missing keys.

## How to get to it (user POV)

- Run `uv run python config-generator.py` in the checkout.
- At `Are you just updating to grab any new UA config options? (Y/n):`, press Enter or type `Y`.

## Driving it with ua-verify

Preconditions:

- `ua-verify doctor` prints `doctor ok`.
- `$UA_DATA_DIR/data/config.py` already exists from launch.
- The generator is answering only that one question. This recipe does not walk the fresh-setup prompts.

- **Start generator.** Run `.cursor/skills/verify-upload-assistant/bin/ua-verify drive config-update`. The helper sends `Y`.
- **Written file.** Stdout contains `Configuration file created` and the exit file is `0`.
- **Confirm the file.** `$UA_DATA_DIR/data/config.py` still exists after the command. A backup may appear as `config.py.bak` in the same directory.
- **Proof.** Keep `artifacts/config-update/stdout.txt` and its exit file. Do not copy the config into the evidence directory.

## Gotchas

- Answering `n` leaves the update path and starts the interactive editor. That is a different journey.
- The generator follows `UA_DATA_DIR`. Without it, the same command edits the user state directory.
- If the checkout contains `data/config.py`, startup may already have merged settings into the disposable file before the generator runs.
- A written-file message is not proof of tracker credentials. The example seed still has empty API fields.
