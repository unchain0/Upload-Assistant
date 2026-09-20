from pathlib import Path
from threading import Event, Lock

from scripts import run_coverage_shards as sharding
from src.domain_models.tracker_catalog import KNOWN_TRACKERS


def _targets() -> tuple[list[sharding.CoverageShard], list[str]]:
    shards = sharding.discover_shards()
    targets = [target for shard in shards for target in shard.targets]
    return shards, targets


def test_tracker_contract_targets_cover_every_known_tracker() -> None:
    _shards, targets = _targets()
    tracker_targets = {
        target
        for target in targets
        if target.startswith(sharding.TRACKER_CONTRACT_FILE)
    }
    expected_effect = {
        f"{sharding.TRACKER_EFFECT_PREFIX}[{tracker}]"
        for tracker in KNOWN_TRACKERS
    }
    expected_private = {
        f"{sharding.TRACKER_PRIVATE_PREFIX}[{tracker}]"
        for tracker in KNOWN_TRACKERS
    }

    assert tracker_targets == {
        sharding.TRACKER_DETERMINISTIC_NODE,
        *expected_effect,
        *expected_private,
    }
    assert len(tracker_targets) == 1 + 2 * len(KNOWN_TRACKERS)


def test_tracker_contract_targets_respect_shard_size() -> None:
    shards, _targets_list = _targets()
    counts = [
        sum(
            target.startswith(sharding.TRACKER_CONTRACT_FILE)
            for target in shard.targets
        )
        for shard in shards
    ]
    assert all(
        count <= sharding.TRACKER_CONTRACT_SHARD_SIZE for count in counts
    )


def test_coverage_shards_represent_every_test_file() -> None:
    shards, targets = _targets()
    represented_files = {
        target.split("::", maxsplit=1)[0] for target in targets
    }
    expected_files = {
        path.relative_to(sharding.ROOT).as_posix()
        for path in Path(sharding.TESTS_DIR).glob("test_*.py")
    }

    assert represented_files == expected_files
    assert [shard.index for shard in shards] == list(range(len(shards)))


def test_chunking_is_ordered_and_does_not_drop_values() -> None:
    assert sharding._chunks(["a", "b", "c", "d", "e"], 2) == [
        ("a", "b"),
        ("c", "d"),
        ("e",),
    ]


def test_run_all_starts_multiple_shards_with_multiple_workers(
    monkeypatch,
) -> None:
    started = 0
    lock = Lock()
    both_started = Event()
    shards = [
        sharding.TestShard(index=0, targets=("one",)),
        sharding.TestShard(index=1, targets=("two",)),
    ]

    def run_shard(_shard: sharding.TestShard) -> None:
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                both_started.set()
        assert both_started.wait(timeout=1)

    monkeypatch.setattr(sharding, "prepare_parts", lambda: None)
    monkeypatch.setattr(sharding, "run_shard", run_shard)
    monkeypatch.setattr(
        sharding, "combine_and_report", lambda *, fail_under: fail_under
    )

    assert sharding._run_all(shards, fail_under=100, jobs=2) == 0


def test_run_partition_selects_deterministic_shards(monkeypatch) -> None:
    shards = [
        sharding.TestShard(index=index, targets=(str(index),))
        for index in range(5)
    ]
    selected: list[int] = []

    monkeypatch.setattr(sharding, "prepare_parts", lambda: None)
    monkeypatch.setattr(
        sharding, "run_shard", lambda shard: selected.append(shard.index)
    )

    assert sharding._run_partition(1, 3, shards) == 0
    assert selected == [1, 4]
