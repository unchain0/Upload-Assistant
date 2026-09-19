# PROJECT KNOWLEDGE BASE

**Updated:** 2026-09-03

## OVERVIEW

Upload-Assistant is a Python 3.14+ CLI for preparing media releases, resolving metadata, checking duplicates, creating torrent/Usenet artifacts, and uploading to tracker/indexer integrations. The current codebase uses a MASA-style layered architecture: pure domain models and engines, use-case services, infrastructure integrations, a thin CLI delivery layer, and `src/bootstrap.py` as the composition root for shared application services.

There is no Flask/Waitress WebUI in the current tree. Do not rely on the pre-MASA module layout (`src/meta.py`, `src/prep.py`, `src/trackersetup.py`, `web_ui/`, etc.); those paths are obsolete.

## STRUCTURE

```text
Upload-Assistant/
├── upload.py                  # CLI entrypoint and release orchestration
├── config-generator.py        # Interactive configuration generator/migrator
├── src/
│   ├── domain_models/         # Pure release/configuration/domain types and errors
│   ├── engines/               # Pure deterministic policies/calculations
│   ├── services/              # Application use cases and consumer-owned ports
│   ├── integrations/          # External APIs, filesystem, media, trackers, clients, runtime tools
│   ├── delivery/cli/          # CLI argument/schema boundary
│   └── bootstrap.py           # Composition root for shared services
├── tests/                     # Pytest regression and edge-case suites
├── scripts/                   # Quality gates, packaging and maintenance helpers
├── docs/                      # User and architecture documentation
└── data/                      # Shipped defaults plus ignored runtime/user state
```

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| CLI execution/orchestration | `upload.py`, `src/delivery/cli/arguments.py` | Keep transport parsing separate from domain/use-case logic |
| Release state and IDs | `src/domain_models/release.py`, `src/domain_models/ids.py` | Domain types must not import integrations/delivery |
| Preparation workflow | `src/services/preparation_service.py`, category preparation services | Services coordinate effects; pure rules belong in engines |
| Pure policies | `src/engines/` | No filesystem/network/framework I/O |
| Metadata providers | `src/integrations/external_apis/`, `src/services/metadata_service.py` | Provider DTOs/errors must not leak into services |
| Screenshots/artwork/spectrograms | `src/integrations/media/`, `src/integrations/filesystem/temp_paths.py` | Keep generated artifacts in typed temp locations |
| Tracker lifecycle | `src/services/tracker_*`, `src/integrations/trackers/` | Registry/adapters are integrations; orchestration is a service concern |
| Torrent clients | `src/integrations/torrent_clients/` | Path mapping and client-specific TLS/connection behavior stay here |
| Runtime binary downloads | `src/integrations/runtime_tools/` | Downloads are bounded/checksum-verified; archive extraction must stay confined |
| Configuration | `src/domain_models/configuration.py`, `src/services/configuration_*`, `src/integrations/configuration/`, `src/bootstrap.py` | `data/config.py` is mutable user state, not source schema |
| MASA rules | `docs/architecture/masa.md`, `scripts/check_masa_architecture.py` | Preserve the import graph when changing layers |

## ARCHITECTURE RULES

- `domain_models` is pure and imports none of the other application layers.
- `engines` imports domain types only and must remain deterministic and I/O-free.
- `services` orchestrates use cases with explicit dependencies/ports and domain contracts.
- `integrations` owns HTTP/SDK/DB/filesystem/subprocess details, mapping, and infrastructure-error translation.
- `delivery` validates/parses transport input and delegates to services; it must not call integrations directly.
- `src/bootstrap.py` may know concrete integrations for dependency wiring but must not contain business rules.
- External/raw data should be converted to domain types at boundaries; do not introduce new untyped payload contracts in services/engines.

## PROJECT CONVENTIONS

- Python target: 3.14+.
- Dependency/environment management is `uv`; do not add a parallel pip/requirements workflow.
- Use absolute first-party imports (`src...`).
- Preserve async boundaries. Move blocking filesystem, archive, hashing, subprocess, image/media, or other heavy work off the event loop with the existing `asyncio.to_thread` pattern when appropriate.
- Network downloads must have bounded size/timeouts; executable/runtime-tool downloads must keep checksum verification and safe archive extraction.
- Never disable TLS verification except when an explicit user configuration requests it; normalize string configuration before truthiness decisions.
- Treat `data/config.py`, cookies, auth/session files, caches and `tmp/` as runtime/user state that may contain secrets.
- Keep tracker-specific policy in tracker integrations or tracker services rather than generic provider/domain modules.
- Prefer focused regression tests for every bug/edge case. Tests commonly use `tmp_path`, `monkeypatch`, `AsyncMock`, and small fakes.

## VALIDATION

Use the project's existing tools rather than adding overlapping linters/typecheckers:

```bash
uv lock --check
uv run python scripts/check_repository_policy.py
uv run python -m compileall -q src upload.py config-generator.py scripts
uv run ruff format --check .
uv run ruff check .
uv run python scripts/check_radon_complexity.py
uv run basedpyright
uv run python scripts/check_masa_architecture.py
uv run pytest -q
```

`scripts/run-quality-gates.sh` aggregates the complete gate, including coverage. On memory-constrained systems, run the same checks serially and run pytest in small file batches instead of using parallel workers.

The repository enforces Radon rank A / cyclomatic complexity <= 5 and MASA import boundaries. `scripts/run_coverage_shards.py` exists for coverage collection; do not increase concurrency merely to make the suite faster on memory-constrained hosts.

## SAFETY / ANTI-PATTERNS

- Do not use unsafe archive member extraction (`extract`/`extractall`) for untrusted archives without confinement and size limits.
- Do not read large downloaded/extracted binaries wholly into memory when streaming/hash-on-disk is sufficient.
- Do not use `shell=True`, `os.system`, `eval`, `exec`, unsafe pickle/YAML loading, or unbounded external downloads.
- Do not swallow infrastructure errors silently; log actionable context or translate them to the appropriate domain/application outcome.
- Do not add global mutable collaborators/service locators; dependencies should be visible in constructors/factories/composition wiring.
- Do not revert or normalize unrelated user changes just to reduce a diff.
