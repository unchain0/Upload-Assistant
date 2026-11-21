# Upstream sync disposition for the MASA CLI

This sync follows upstream development through 5.1 (`97e9537d`) while keeping the fork's CLI-only architecture.

## Behavior owners kept

| Upstream source | MASA owner |
| --- | --- |
| #900 `c1853ac1`, #903 `a7befc92`, #904 `bffa0520`, #906 `14ba2a5f` — local upload history and saved IDs | `src/upload_history.py` and `src/manual_input.py`; the History page from #901 is discarded. |
| #892 `e3677cf7` — aggregate statistics | `src/stats.py`; browser dashboards and event presentation are discarded. |
| #939 `16d66fdc` — upload pacing | `src/upload_schedule.py`; waits report through the CLI console. |
| #897 `8e91a847` — reuse content from another qBittorrent client | `src/integrations/torrent_clients/existing_content.py` and `src/resource_coordinator.py`. |
| #861 `225a18fa` — typed tracker payload fields and validation | `src/integrations/trackers/payload_preview.py` and tracker integrations; transport-specific browser rendering is not part of this sync. |
| #55 `5c5efb16` — omit the fork signature when disabled | `DescriptionBuilder._ua_signature_part`; `tests/test_description_builder_edges.py` covers empty and explicit signatures. |

Reusable text redaction formerly called through `src/webui_events.py` now belongs to `src/integrations/security/redaction.py`. Tracker-status classification belongs to `src/upload_history.py`. Manual and trump prompts keep their CLI interactions and no longer emit browser events.

The history and statistics modules are retained in their application-owned MASA modules, but this sync does not add lifecycle wiring to `upload.py`; their UI-only presentation is discarded. Do not describe those views as available in the CLI.

## Discarded UI-only changes

| Upstream source | Disposition |
| --- | --- |
| `627af6b2` — 409 legacy `web_ui/` assets and `docs/web-ui.md` | Kept excluded; MASA has no Flask/Waitress application. |
| #908 `0c19e426` — Unraid and ZimaOS templates that start `--webui` | Removed, together with the WebUI-only Docker guide. |
| #709 `3f2f8cc8` — generator for flags under `web_ui/static/` | Removed because its inputs and output are deleted UI assets. |
| WebUI event/path modules, JavaScript tests, and the old shutdown test harness | Removed; the shutdown regression coverage now tests the MASA CLI signal handlers directly. |
| #368 `7d2f4bbd` — legacy FLOOD tracker module | Not restored: it depends on `src.meta`, `src.trackers`, and the old description-review path. Empty-signature behavior remains covered by the generic MASA description builder. |

## Fork customizations kept

Commits #50 (`2f272286`), #52 (`fbd149c5`), #54 (`9f716d8b`), #55 (`5c5efb16`), #56 (`c0669931`), and #58–#60 (`b783d79f`, `d66f3231`, `a11cbd09`) remain in the sync history and are not reverted by the removals above.
