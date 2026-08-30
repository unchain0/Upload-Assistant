"""Run the pytest suite in deterministic, memory-bounded coverage shards."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TESTS_DIR = ROOT / "tests"
ARTIFACTS_DIR = ROOT / "artifacts"
PARTS_DIR = ARTIFACTS_DIR / "coverage-parts"
COMBINED_DATA = ARTIFACTS_DIR / ".coverage"
COVERAGE_JSON = ARTIFACTS_DIR / "coverage.json"
COVERAGE_XML = ARTIFACTS_DIR / "coverage.xml"
REGULAR_SHARD_SIZE = 8
TRACKER_CONTRACT_SHARD_SIZE = 15
TRACKER_CONTRACT_FILE = "tests/test_tracker_adapter_contracts.py"
TRACKER_DETERMINISTIC_NODE = f"{TRACKER_CONTRACT_FILE}::test_tracker_catalog_deterministic_rules_accept_domain_fixtures"
TRACKER_EFFECT_PREFIX = f"{TRACKER_CONTRACT_FILE}::test_tracker_effect_boundary_is_exercised_with_fakes"
TRACKER_PRIVATE_PREFIX = f"{TRACKER_CONTRACT_FILE}::test_tracker_private_helpers_use_domain_fixtures_without_terminating"


@dataclass(frozen=True, slots=True)
class TestShard:
    index: int
    targets: tuple[str, ...]

    @property
    def data_file(self) -> Path:
        return PARTS_DIR / f".coverage.{self.index:03d}"


def _chunks(values: list[str], size: int) -> list[tuple[str, ...]]:
    return [
        tuple(values[offset : offset + size])
        for offset in range(0, len(values), size)
    ]


def _tracker_contract_groups() -> list[tuple[str, ...]]:
    from src.domain_models.tracker_catalog import KNOWN_TRACKERS

    trackers = sorted(KNOWN_TRACKERS)
    effect_nodes = [
        f"{TRACKER_EFFECT_PREFIX}[{tracker}]" for tracker in trackers
    ]
    private_nodes = [
        f"{TRACKER_PRIVATE_PREFIX}[{tracker}]" for tracker in trackers
    ]
    return [
        (TRACKER_DETERMINISTIC_NODE,),
        *_chunks(effect_nodes, TRACKER_CONTRACT_SHARD_SIZE),
        *_chunks(private_nodes, TRACKER_CONTRACT_SHARD_SIZE),
    ]


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def _regular_groups(
    files: list[Path], excluded: set[Path]
) -> list[tuple[str, ...]]:
    regular = [path for path in files if path not in excluded]
    names = [_relative(path) for path in regular]
    return _chunks(names, REGULAR_SHARD_SIZE)


def _contract_groups(
    files: list[Path], tracker_contract_path: Path
) -> list[tuple[str, ...]]:
    contract_files = [
        path
        for path in files
        if "contract" in path.stem and path != tracker_contract_path
    ]
    return [(_relative(path),) for path in contract_files]


def discover_shards() -> list[TestShard]:
    files = sorted(TESTS_DIR.glob("test_*.py"))
    tracker_contract_path = TESTS_DIR / Path(TRACKER_CONTRACT_FILE).name
    contract_groups = _contract_groups(files, tracker_contract_path)
    excluded = {
        tracker_contract_path,
        *(path for path in files if "contract" in path.stem),
    }
    groups = [*_regular_groups(files, excluded), *contract_groups]
    if tracker_contract_path.is_file():
        groups.extend(_tracker_contract_groups())
    return [
        TestShard(index=index, targets=group)
        for index, group in enumerate(groups)
    ]


def prepare_parts() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(PARTS_DIR, ignore_errors=True)
    shutil.rmtree(ARTIFACTS_DIR / "pytest-tmp", ignore_errors=True)
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    COMBINED_DATA.unlink(missing_ok=True)
    COVERAGE_JSON.unlink(missing_ok=True)
    COVERAGE_XML.unlink(missing_ok=True)


def run_shard(shard: TestShard) -> None:
    shard.data_file.unlink(missing_ok=True)
    basetemp = ARTIFACTS_DIR / "pytest-tmp" / f"shard-{shard.index:03d}"
    shutil.rmtree(basetemp, ignore_errors=True)
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"\n== coverage shard {shard.index:03d}: {', '.join(shard.targets)} ==",
        flush=True,
    )
    command = [
        "uv",
        "run",
        "coverage",
        "run",
        f"--data-file={shard.data_file}",
        "--source=src",
        "-m",
        "pytest",
        "-q",
        f"--basetemp={basetemp}",
        *shard.targets,
    ]
    # uv/coverage argv is fixed; pytest node IDs are discovered from local test files.
    completed = subprocess.run(  # noqa: S603  # nosemgrep: dangerous-subprocess-use-audit
        command, cwd=ROOT, env=os.environ.copy(), check=False
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)
    if not shard.data_file.is_file():
        raise RuntimeError(f"Coverage shard did not create {shard.data_file}")


def combine_and_report(*, fail_under: float) -> float:
    parts = sorted(PARTS_DIR.glob(".coverage.*"))
    expected = len(discover_shards())
    if len(parts) != expected:
        raise RuntimeError(
            f"Expected {expected} coverage shards, found {len(parts)}"
        )
    coverage = Coverage(
        data_file=str(COMBINED_DATA), config_file=str(ROOT / "pyproject.toml")
    )
    coverage.combine(data_paths=[str(PARTS_DIR)], strict=True, keep=True)
    coverage.save()
    coverage.json_report(outfile=str(COVERAGE_JSON), pretty_print=True)
    coverage.xml_report(outfile=str(COVERAGE_XML))
    total = coverage.report(show_missing=True, skip_covered=False)
    print(f"\nCombined line coverage: {total:.2f}%", flush=True)
    if round(total, 2) < fail_under:
        raise SystemExit(
            f"Combined line coverage {total:.2f}% is below the required {fail_under:.2f}%"
        )
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="List deterministic shards and exit",
    )
    parser.add_argument("--shard", type=int, help="Run only one shard index")
    parser.add_argument(
        "--combine-only",
        action="store_true",
        help="Combine existing shard data without running tests",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Reset coverage parts before a sharded local run",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=100.0,
        help="Required combined line coverage percentage",
    )
    return parser.parse_args()


def _list_shards(shards: list[TestShard]) -> int:
    for shard in shards:
        print(f"{shard.index:03d}: {' '.join(shard.targets)}")
    return 0


def _run_requested_shard(index: int, shards: list[TestShard]) -> int:
    if index < 0 or index >= len(shards):
        print(
            f"Unknown shard {index}; valid range is 0..{len(shards) - 1}",
            file=sys.stderr,
        )
        return 2
    run_shard(shards[index])
    return 0


def _run_all(shards: list[TestShard], fail_under: float) -> int:
    prepare_parts()
    for shard in shards:
        run_shard(shard)
    combine_and_report(fail_under=fail_under)
    return 0


def _dispatch(args: argparse.Namespace, shards: list[TestShard]) -> int:
    if args.list:
        return _list_shards(shards)
    if args.prepare:
        prepare_parts()
    if args.combine_only:
        combine_and_report(fail_under=args.fail_under)
        return 0
    if args.shard is not None:
        return _run_requested_shard(args.shard, shards)
    return _run_all(shards, args.fail_under)


def main() -> int:
    args = parse_args()
    return _dispatch(args, discover_shards())


if __name__ == "__main__":
    raise SystemExit(main())
