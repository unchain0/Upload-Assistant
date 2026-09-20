# MASA architecture

Upload Assistant is a CLI-only modular monolith organized around the five MASA layers. Directory names, imports, constructor dependencies, and public types are treated as executable architecture: the repository fails its quality gate when a forbidden edge is introduced.

## Runtime flow

1. `delivery/cli` parses terminal input and converts it into domain values.
2. `services` coordinate preparation, metadata, duplicate checks, artifact creation, tracker selection, and upload cases.
3. `engines` apply deterministic naming, validation, selection, redaction, and safety policies.
4. `integrations` own filesystem, HTTP, SDK, subprocess, tracker, torrent-client, media-tool, and image-host effects.
5. The composition root builds the concrete graph and the CLI presents domain outcomes.

User configuration, caches, logs, and temporary artifacts live in the runtime state directory selected by `UA_DATA_DIR` or by the platform data-directory convention. The source checkout is not runtime state.

## Layers and dependency matrix

| Layer | Responsibility | Allowed project dependencies |
|---|---|---|
| `domain_models` | Immutable vocabulary, typed identities, and semantic errors | `domain_models` |
| `engines` | Pure deterministic rules and transformations | `domain_models`, `engines` |
| `services` | Use-case orchestration and consumer-owned contracts | `domain_models`, `engines`, `services`, and explicit integration adapters used by the case |
| `integrations` | Filesystem, network, SDK, subprocess, tracker/client, and media-tool boundaries | `domain_models`, `integrations` |
| `delivery` | CLI parsing, validation, prompting, and presentation | `domain_models`, `services`, `delivery` |

Concrete wiring is confined to `src/bootstrap.py` and the root CLI entrypoints. `scripts/check_masa_architecture.py` parses the complete `src` tree without importing application modules and fails on forbidden edges. `tests/test_masa_architecture.py` locks the matrix and the zero-violation requirement.

Services may refer to a concrete adapter only when that dependency is explicit and the adapter returns domain values. Infrastructure exceptions and SDK/ORM payloads still may not escape integrations. New stateful collaborators should be represented by consumer-owned protocols and injected through constructors.

## Configuration boundary

`ConfigurationService` reconciles three sources:

- the user-owned runtime configuration;
- a legacy checkout configuration when it contains configured values absent from runtime state;
- the bundled example schema, used only to add missing keys.

Non-empty runtime values win. Reconciliation writes atomically and preserves the previous runtime file as `config.py.pre-masa.bak`. Every CLI execution loads the same materialized runtime configuration instead of relying on Python module-cache or import-path order.

## TMDb authentication and image-host fallback

TMDb credentials are normalized at the boundary. V3 API keys are trimmed and sent as an `api_key` query parameter; V4 read-access tokens are sent as bearer authentication. The active configuration path is resolved before the TMDb client is created, preventing a valid key in one file from being shadowed by a stale runtime copy.

Image uploads use a deterministic tracker-compatible fallback plan. The selected host is attempted first, followed by every other configured compatible host, including hosts in earlier numbered slots. Partial successes are retained. Rate limits, credential rejection, and provider outages open a run-level circuit so the batch advances without repeatedly hammering the failed provider.

## Quality and measurability

`scripts/run-quality-gates.sh` runs the locked `uv` environment, Python compilation, Ruff, a hard Radon rank-A cyclomatic-complexity gate (every block CC <= 5), BasedPyright, the MASA boundary audit, the complete pytest suite, and a hard 100% line-coverage gate. `scripts/generate_quality_report.py` records per-layer modules, physical lines, AST statements, functions, classes, average/maximum cyclomatic complexity, dependency-matrix counts, test counts, coverage totals, and architecture violations as JSON and Markdown.

`.github/workflows/masa-quality-gate.yml` executes the same gate for pull requests and pushes to `development`; therefore neither pre-merge nor post-merge CI can pass below 100% measured line coverage. Evidence is uploaded as a workflow artifact even when another check fails.
