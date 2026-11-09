# Debug dry-run

Debug dry-run lets a person walk a local file through preparation and see the log, while the run does not upload to trackers and does not add the torrent to the client.

## Sub-features

- `debug-prepare` runs preparation on one local file.
- `debug-no-upload` does not post the release to a tracker.
- `debug-no-seed` does not hand the torrent to the torrent client.

## How to get to it (user POV)

- Run `uv run python upload.py "/path/to/file" --debug --unattended --no-seed`.

## Driving it with ua-verify

Preconditions:

- `ua-verify doctor` prints `doctor ok`.
- `UA_VERIFY_MEDIA` is an existing local video file, not a title and not a tracker URL.
- `UA_VERIFY_ALLOW_DRY_RUN=1` is set, because the run still calls metadata services.
- The disposable config has a TMDb key if the file is a movie or episode. The example seed does not.

- **Prepare.** Point the CLI at the file. Run `.cursor/skills/verify-upload-assistant/bin/ua-verify drive debug-dry-run`. The transcript is `artifacts/debug-dry-run/stdout.txt` and its exit file.
- **No client torrent.** After the process exits, the transcript does not contain a line that says the torrent was added to qBittorrent. New files, if any, are under `$UA_DATA_DIR/tmp`.
- **No tracker upload.** The transcript does not contain an upload success URL from a tracker.
- **Proof.** Save the transcript, the exit code, and the names of files created under `$UA_DATA_DIR/tmp`. A non-zero exit is a failed preparation, not a successful dry-run.

## Gotchas

- `--debug` still contacts TMDb and srrDB when preparation reaches them. The flag only skips the tracker upload.
- `--no-seed` can still create a torrent file in the disposable tmp directory.
- The example config has an empty `tmdb_api`. A movie or episode run fails that precondition instead of counting as verified.
- Do not point `UA_VERIFY_MEDIA` at the user's download library if the disposable config was reconciled with real tracker credentials.
- Refusing to run without `UA_VERIFY_MEDIA` is the correct result for the baseline seed.
