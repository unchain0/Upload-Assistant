# CORE PACKAGE KNOWLEDGE BASE

## OVERVIEW

`src` is organized as a MASA-style application package. The current flow is not the old flat `src/meta.py` / `src/prep.py` / `src/trackersetup.py` design. Domain vocabulary lives in `domain_models`, pure policy in `engines`, use-case orchestration in `services`, external effects in `integrations`, CLI transport in `delivery`, and concrete wiring in `bootstrap.py`.

## LAYERS

| Layer | Responsibility | May depend on |
| --- | --- | --- |
| `domain_models/` | Pure release/configuration/value types, IDs, semantic errors | Standard library / domain-local code |
| `engines/` | Pure deterministic rules and policies | `domain_models` |
| `services/` | Use cases, orchestration, consumer-owned ports | `domain_models`, `engines`, declared ports |
| `integrations/` | HTTP/SDK/filesystem/media/subprocess/tracker/client adapters | Domain contracts plus external libraries |
| `delivery/` | CLI parsing/validation and transport mapping | Domain/service interfaces; no direct integration calls |
| `bootstrap.py` | Composition root | Concrete integrations/services for wiring only |

The canonical architecture description and enforcement are `docs/architecture/masa.md` and `scripts/check_masa_architecture.py`.

## WHERE TO LOOK

| Task | Location |
| --- | --- |
| Shared release model/state | `domain_models/release.py` |
| Typed IDs and identity | `domain_models/ids.py`, `domain_models/release_identity.py` |
| Semantic errors | `domain_models/errors.py` |
| Preparation orchestration | `services/preparation_service.py`, category-specific preparation services |
| Metadata orchestration | `services/metadata_service.py`, `services/tracker_metadata_*` |
| Duplicate/upload decisions | `services/duplicate_check_service.py`, `services/upload_decision_service.py` |
| Pure upload/tracker policies | `engines/tracker_upload_eligibility.py`, `engines/upload_safety_policy.py` |
| External metadata providers | `integrations/external_apis/` |
| Media/artwork/screenshots | `integrations/media/` |
| Temp/cache/filesystem paths | `integrations/filesystem/`, `integrations/cache/` |
| Runtime binaries/download integrity | `integrations/runtime_tools/` |
| Torrent clients | `integrations/torrent_clients/` |
| Tracker implementations | `integrations/trackers/` |
| Usenet/torrent artifact creation | `integrations/usenet/`, `integrations/torrent/` |
| CLI argument boundary | `delivery/cli/arguments.py` |
| Configuration wiring | `bootstrap.py`, `services/configuration_*`, `integrations/configuration/` |

## RULES FOR CHANGES

- Convert external payloads to domain-shaped data at integration/delivery boundaries; do not propagate SDK/HTTP/raw dict contracts into engines.
- Keep engines pure: no HTTP, filesystem, subprocesses, clocks/randomness with hidden state, logging side effects that affect behavior, or async wrappers around I/O.
- Services coordinate effects but should declare their dependencies explicitly. Avoid globals, service locators, and concrete infrastructure imports when a consumer-owned port is appropriate.
- Delivery remains thin and must not call tracker/client/media integrations directly.
- Integrations own timeouts, retries, bounded downloads, TLS behavior, archive confinement, error translation and external-format mapping.
- Runtime-tool installers must preserve checksum verification, staging/rollback behavior, safe regular-file extraction, executable-bit handling, and cleanup on exceptions/cancellation.
- Heavy blocking work invoked from async code should use the existing `asyncio.to_thread` pattern unless the library provides a truly asynchronous API.
- Generated images/artifacts should use helpers in `integrations/filesystem/temp_paths.py` rather than ad-hoc release-root paths.
- For configuration booleans arriving as strings, normalize recognized true/false spellings before applying Python truthiness.

## TESTING

Prefer the smallest test file that owns the behavior, then expand to the affected subsystem. Typical patterns include `tmp_path`, `monkeypatch`, `AsyncMock`, fake adapters and explicit edge-case assertions.

Useful validation for `src` changes:

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright src
uv run python scripts/check_masa_architecture.py
uv run python scripts/check_radon_complexity.py
uv run pytest -q tests/test_<affected_area>.py
```

When RAM is constrained, keep pytest serial and run files/batches separately. Do not introduce `pytest-xdist` or increase worker counts just to reduce wall-clock time.

## ANTI-PATTERNS

- No reintroduction of removed flat modules merely to avoid following the current layer boundaries.
- No `ZipFile.extract*`/`TarFile.extract*` on untrusted archives without explicit confinement, regular-file checks and size limits.
- No executable download without pinned integrity verification.
- No `verify=False`/`CERT_NONE` as a hidden default; disabling certificate checks must be an explicit user configuration choice.
- No large `archive.read()`/`response.content`/captured subprocess payload when bounded streaming to disk is practical.
- No broad process cleanup or killing by pattern; cleanup code may only act on task-owned/tracked resources.
