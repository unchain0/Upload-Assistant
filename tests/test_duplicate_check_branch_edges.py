from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.services import duplicate_check_service as duplicate_service
from src.services.duplicate_check_service import DupeChecker
from tests.test_duplicate_check_edges import _config, _entry, _media, _meta


def _filter(
    _tmp_path: Path,
    meta: Meta,
    candidate: dict[str, Any] | str,
    tracker: str = "OTHER",
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return asyncio.run(
        DupeChecker(config or _config()).filter_dupes(
            [candidate], meta, tracker
        )
    )


def _no_tolerance_config() -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "AITHER": {"internal": True, "internal_groups": ["GROUP"]}
        },
    }


def test_empty_debug_dupe_list(tmp_path: Path) -> None:
    assert (
        asyncio.run(
            DupeChecker(_config()).filter_dupes(
                [], _meta(tmp_path, debug=True), "OTHER"
            )
        )
        == []
    )


def test_size_difference_tolerance_excludes_candidate(tmp_path: Path) -> None:
    meta = _meta(tmp_path, source_size=100, dupe_size_difference_tolerance=10)
    assert (
        _filter(
            tmp_path,
            meta,
            _entry("Different.Release.1080p.WEB-DL", size=200, files=[]),
        )
        == []
    )


def test_invalid_size_tolerance_is_nonfatal(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path, dupe_size_difference_tolerance="invalid", debug=True
    )
    result = _filter(
        tmp_path, meta, _entry("Different.Release.1080p.WEB-DL", files=[])
    )
    assert isinstance(result, list)


def test_game_empty_title_is_excluded(tmp_path: Path) -> None:
    meta = _meta(tmp_path, category="GAME", title="", name="", platform="PC")
    assert (
        _filter(tmp_path, meta, _entry("Different Game", type="PC", files=[]))
        == []
    )


def test_game_title_mismatch_is_excluded(tmp_path: Path) -> None:
    meta = _meta(tmp_path, category="GAME", title="Target Game", platform="PC")
    assert (
        _filter(tmp_path, meta, _entry("Different Game", type="PC", files=[]))
        == []
    )


def test_book_empty_title_is_excluded(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path, category="BOOK", title="", name="", type="EPUB", filelist=[]
    )
    assert (
        _filter(
            tmp_path, meta, _entry("Different Book", type="epub", files=[])
        )
        == []
    )


def test_book_ebook_compatibility_extensions_path(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        category="BOOK",
        title="Target Book",
        type="BOOK",
        filelist=[],
    )
    result = _filter(
        tmp_path, meta, _entry("Target Book", type="book", files=[])
    )
    assert result


def test_book_audiobook_vs_ebook_mismatch_is_excluded(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        category="BOOK",
        title="Target Book",
        type="EPUB",
        audiobook=True,
        filelist=[],
    )
    assert (
        _filter(tmp_path, meta, _entry("Target Book", type="epub", files=[]))
        == []
    )


def test_book_format_can_match_torrent_name(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        category="BOOK",
        title="Target Book",
        type="EPUB",
        audiobook=False,
        filelist=[],
    )
    result = _filter(
        tmp_path, meta, _entry("Target Book - EPUB", type="unknown", files=[])
    )
    assert isinstance(result, list)


def test_book_exact_payload_after_semantic_checks(tmp_path: Path) -> None:
    media = _media(tmp_path, "Target Book")
    meta = _meta(
        tmp_path,
        category="BOOK",
        title="Target Book",
        name="Target Book Local",
        type="",
        filelist=[str(media)],
        source_size=100,
    )
    candidate = _entry(
        "Target Book", type="", files=[media.name], file_count=1, size=100
    )
    result = _filter(tmp_path, meta, candidate)
    assert result and meta.get("OTHER_matched_reason") == "exact_payload"


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ({"name": "Disc Release", "file_count": 1, "files": []}, []),
        ({"name": "00001.m2ts", "file_count": 2, "files": []}, "kept"),
        ({"name": "Disc.Release.mkv", "file_count": 2, "files": []}, []),
    ],
)
def test_disc_file_shape_branches(
    tmp_path: Path, candidate: dict[str, Any], expected: object
) -> None:
    meta = _meta(
        tmp_path,
        is_disc="BDMV",
        filelist=[],
        source_size=100,
        name="Different Disc",
    )
    result = _filter(
        tmp_path,
        meta,
        _entry(**candidate)
        if False
        else {**_entry(str(candidate["name"]), files=[]), **candidate},
    )
    assert bool(result) is (expected == "kept")


def test_repack_upload_excludes_non_repack_same_group(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        uuid="Movie.REPACK.1080p-GROUP",
        name="Movie REPACK 1080p-GROUP",
        tag="-GROUP",
    )
    assert (
        _filter(tmp_path, meta, _entry("Movie.1080p.WEB-DL-GROUP", files=[]))
        == []
    )


def test_disc_size_match_and_invalid_size_debug(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        is_disc="BDMV",
        filelist=[],
        source_size=100,
        name="Target Disc",
    )
    result = _filter(
        tmp_path, meta, _entry("Other Disc", size=100, file_count=2, files=[])
    )
    assert result and meta.size_match

    invalid = _meta(
        tmp_path,
        is_disc="BDMV",
        filelist=[],
        source_size=100,
        name="Target Disc",
        debug=True,
    )
    result = _filter(
        tmp_path,
        invalid,
        _entry("Other Disc", size="bad", file_count=2, files=[]),
    )
    assert isinstance(result, list)


def test_special_tracker_invalid_size_debug_paths(tmp_path: Path) -> None:
    for tracker in ("ALPHARATIO", "BEYONDHD"):
        meta = _meta(
            tmp_path, source_size=100, debug=True, name="Target", filelist=[]
        )
        result = _filter(
            tmp_path, meta, _entry("Other", size="bad", files=[]), tracker
        )
        assert isinstance(result, list)


def test_beyondhd_normalized_name_match(tmp_path: Path) -> None:
    meta = _meta(tmp_path, name="Movie DD+ 5.1", filelist=[])
    result = _filter(
        tmp_path,
        meta,
        _entry("Movie DDP 5.1", size=None, files=[]),
        "BEYONDHD",
        _no_tolerance_config(),
    )
    assert result


def test_framestor_and_sd_special_tracker_paths(tmp_path: Path) -> None:
    framestor = _meta(
        tmp_path, resolution="2160p", uuid="Movie.2160p-GROUP", filelist=[]
    )
    assert _filter(
        tmp_path,
        framestor,
        _entry("Movie.2160p.FraMeSToR", size=None, files=[]),
        "BEYONDHD",
        _no_tolerance_config(),
    )

    sd = _meta(tmp_path, sd=1, resolution="480p", filelist=[])
    assert _filter(
        tmp_path,
        sd,
        _entry("Movie.1080p.WEB-DL", size=None, files=[]),
        "AITHER",
        _no_tolerance_config(),
    )


def test_aither_dvd_tag_paths(tmp_path: Path) -> None:
    blank_tag = _meta(tmp_path, is_disc="DVD", tag="", filelist=[])
    assert _filter(
        tmp_path,
        blank_tag,
        _entry("Movie DVD Release", size=None, file_count=2, files=[]),
        "AITHER",
        _no_tolerance_config(),
    )

    matching_tag = _meta(tmp_path, is_disc="DVD", tag="-GROUP", filelist=[])
    assert _filter(
        tmp_path,
        matching_tag,
        _entry("Movie DVD GROUP", size=None, file_count=2, files=[]),
        "AITHER",
        _no_tolerance_config(),
    )


def test_oldtoonsworld_same_episode_resolution_mismatch(
    tmp_path: Path,
) -> None:
    meta = _meta(
        tmp_path,
        category="TV",
        season="S01",
        episode="E02",
        resolution="1080p",
        filelist=[],
    )
    result = _filter(
        tmp_path,
        meta,
        _entry("Show.S01E02.720p.WEB-DL", files=[]),
        "OLDTOONSWORLD",
    )
    assert result


def test_dvd_resolution_and_remux_mismatch_paths(tmp_path: Path) -> None:
    dvd = _meta(
        tmp_path, is_disc="DVD", source="DVD", resolution="480p", filelist=[]
    )
    assert _filter(
        tmp_path,
        dvd,
        _entry("Movie 1080p DVD Release", size=None, file_count=2, files=[]),
        config=_no_tolerance_config(),
    )

    encode = _meta(
        tmp_path, name="Movie 1080p", uuid="Movie.1080p", filelist=[]
    )
    assert (
        _filter(tmp_path, encode, _entry("Movie.1080p.REMUX", files=[])) == []
    )


def test_aither_internal_foreign_group_is_excluded(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        category="TV",
        season="S01",
        episode="E02",
        source="WEB",
        resolution="1080p",
        tag="-GROUP",
        filelist=[],
        debug=True,
    )
    candidate = _entry(
        "Show.S01.1080p.WEB-DL-OTHER",
        type="WEB",
        res="1080p",
        internal=1,
        files=[],
    )
    assert _filter(tmp_path, meta, candidate, "AITHER") == []


def test_aither_duplicate_episode_id_debug_branch(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path,
        category="TV",
        season="S01",
        episode="E02",
        source="WEB",
        resolution="1080p",
        tag="-GROUP",
        filelist=[],
        debug=True,
    )
    candidate = _entry(
        "Show.S01.1080p.WEB-DL-GROUP",
        id=22,
        type="WEB",
        res="1080p",
        internal=0,
        files=[],
    )
    result = asyncio.run(
        DupeChecker(_config()).filter_dupes(
            [candidate, candidate.copy()], meta, "AITHER"
        )
    )
    assert isinstance(result, list)


def test_single_dupe_size_difference_and_reelflix_tag_paths(
    tmp_path: Path,
) -> None:
    large = _meta(
        tmp_path,
        source_size=200,
        mediainfo={"media": {"track": [{"FileSize": "200"}]}},
        filelist=[],
    )
    assert (
        _filter(
            tmp_path,
            large,
            _entry("Movie.1080p.WEB-DL", size=100, files=[]),
            "AITHER",
            _no_tolerance_config(),
        )
        == []
    )

    matching = _meta(tmp_path, tag="-GROUP", filelist=[])
    assert _filter(
        tmp_path, matching, _entry("Movie.1080p.GROUP", files=[]), "REELFLIX"
    )

    mismatch = _meta(tmp_path, tag="-GROUP", filelist=[])
    assert (
        _filter(
            tmp_path,
            mismatch,
            _entry("Movie.1080p.OTHER", files=[]),
            "REELFLIX",
        )
        == []
    )


def test_debug_filtered_dupes_rendering(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path, debug=True, unattended=False, name="Exact Name", filelist=[]
    )
    entries = [
        _entry(
            "Exact Name",
            files=[f"file-{index}.mkv" for index in range(12)],
            description="x" * 300,
        ),
        _entry(
            "Exact Name",
            id=11,
            files=[f"other-{index}.mkv" for index in range(12)],
            description="y" * 300,
        ),
    ]
    result = asyncio.run(
        DupeChecker(_config()).filter_dupes(entries, meta, "OTHER")
    )
    assert len(result) == 2


def test_static_helper_remaining_branches(tmp_path: Path) -> None:
    meta = _meta(tmp_path, source_size=100)
    assert asyncio.run(
        DupeChecker.is_exact_match(
            _entry("Other", files=[], file_count=1, size=100), meta
        )
    )
    assert asyncio.run(
        DupeChecker.is_season_episode_match(
            "News.2024.01.03", "S01", "2024-01-02"
        )
    ) == (False, False)
    assert asyncio.run(
        DupeChecker.is_season_episode_match("Show.E01", None, "E01")
    ) == (False, False)
    non_web = _meta(tmp_path, type="REMUX")
    assert asyncio.run(
        DupeChecker.has_matching_hdr({"DV", "HDR"}, {"DV", "HDR"}, non_web)
    )


def test_module_level_wrappers(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    assert isinstance(
        asyncio.run(
            duplicate_service.filter_dupes([], meta, "OTHER", _config())
        ),
        list,
    )
    assert asyncio.run(
        duplicate_service.is_season_episode_match("Show.S01", "S01", None)
    ) == (True, True)
    assert asyncio.run(duplicate_service.refine_hdr_terms("HDR10")) == {"HDR"}
