# Site check

Site check lets a person search configured trackers for an existing release and write a log, without uploading.

## Sub-features

- `site-check-search` searches the selected trackers.
- `site-check-log` writes one JSON result file per tracker.
- `site-check-no-upload` stops before posting a torrent.

## How to get to it (user POV)

- Run `uv run python upload.py "/path/to/file" --site-check`.
- Add `--trackers NAME` when the search should use one tracker instead of the configured defaults.

## Driving it with ua-verify

Preconditions:

- `ua-verify doctor` prints `doctor ok`.
- The disposable config contains at least one tracker with real credentials. The example seed does not.
- A local media path is available. The baseline seed does not include one.

- **Search.** Run `.cursor/skills/verify-upload-assistant/bin/ua-verify drive site-check`. On the example seed the helper exits 2 and names the missing credentials.
- **Log.** When credentials and a media path exist, a later run must show `$UA_DATA_DIR/tmp/<TRACKER>_search_results.json` for each tracker that was searched.
- **No upload.** The transcript has no upload success URL.
- **Proof.** An unmet-precondition transcript is the proof for the example seed. Do not replace it with a passed `--help`.

## Gotchas

- `--site-check` still uses the tracker accounts in the runtime config. Example placeholders are not a successful search.
- The log path is under the disposable tmp directory, not the checkout.
- A dupe hit is still not an upload. Read the JSON before describing the tracker state.
- Do not run this drive against `~/.local/share/Upload-Assistant`.
