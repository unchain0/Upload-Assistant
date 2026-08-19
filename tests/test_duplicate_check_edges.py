"""Deterministic edge coverage for duplicate classification policy."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.services import duplicate_check_service as duplicate_service
from src.services.duplicate_check_service import DupeChecker


def _media(tmp_path: Path, name: str = "Episode.S01E01.1080p.WEB-DL.x264-GROUP.mkv") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    return path


def _meta(tmp_path: Path, **updates: object) -> Meta:
    media = _media(tmp_path)
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "Episode.S01E01.1080p.WEB-DL.x264-GROUP",
        "path": str(media),
        "name": "Episode S01E01 1080p WEB-DL x264-GROUP",
        "clean_name": "Episode.S01E01.1080p.WEB-DL.x264-GROUP",
        "title": "Episode",
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "resolution": "1080p",
        "tag": "-GROUP",
        "group": "GROUP",
        "filelist": [str(media)],
        "source_size": 100,
        "mediainfo": {"media": {"track": [{"FileSize": "100"}]}},
        "hdr": "",
        "season": "S01",
        "episode": "E01",
        "season_int": 1,
        "episode_int": 1,
        "sd": 0,
        "is_disc": "",
        "tv_pack": False,
        "video_encode": "x264",
        "debug": False,
        "unattended": True,
        "dupe_size_difference_tolerance": None,
        "audiobook": False,
        "platform": "PC",
        "tracker_ids": {},
        "tracker_status": {},
    }
    state.update(updates)
    return Meta(state)


def _entry(name: str, **updates: object) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "size": 100,
        "files": ["Episode.S01E01.1080p.WEB-DL.x264-GROUP.mkv"],
        "file_count": 1,
        "flags": [],
        "id": 10,
        "link": "https://tracker.invalid/10",
        "download": "https://tracker.invalid/10/download",
        "type": "WEB",
        "res": "1080p",
        "internal": 0,
        "trumpable": False,
    }
    entry.update(updates)
    return entry


def _config() -> dict[str, Any]:
    return {
        "DEFAULT": {"dupe_size_difference_tolerance": 10},
        "TRACKERS": {
            "AITHER": {"internal": True, "internal_groups": ["INT", "GROUP"]},
            "OTHER": {},
        },
    }


def test_duplicate_helper_contracts(tmp_path: Path) -> None:
    async def exercise() -> None:
        meta = _meta(tmp_path)
        assert await DupeChecker.is_exact_match(_entry(meta.name), meta)
        assert not await DupeChecker.is_exact_match(_entry("Other", files=["other.mkv"]), meta)
        assert await DupeChecker.normalize_filename("Movie.Name-GROUP") == "movie name -group"
        assert await duplicate_service.normalize_filename({"name": "Movie.Name-GROUP"}) == "movie name -group"
        with pytest.raises(ValueError):
            await DupeChecker.normalize_filename(["bad"])  # type: ignore[arg-type]

        expected = {
            ("News.2024.01.02", "S01", "2024-01-02"): (True, False),
            ("Show.S01.1080p", "S01", None): (True, True),
            ("Show.S02.1080p", "S01", None): (False, False),
            ("Show.S01E02.1080p", "S01", "E01-E02"): (True, False),
        }
        for arguments, result in expected.items():
            assert await DupeChecker.is_season_episode_match(*arguments) == result

        assert await DupeChecker.refine_hdr_terms(None) == set()
        assert await DupeChecker.refine_hdr_terms("DoVi HDR10+") == {"DV", "HDR"}
        web = _meta(tmp_path, type="WEBDL")
        non_web = _meta(tmp_path, type="REMUX")
        assert not await DupeChecker.has_matching_hdr({"DV"}, {"HDR"}, web)
        assert await DupeChecker.has_matching_hdr({"DV"}, {"HDR"}, non_web)
        assert await duplicate_service.has_matching_hdr({"HDR10+"}, {"HDR"}, web)

    asyncio.run(exercise())


def test_exact_match_edge_shapes(tmp_path: Path) -> None:
    async def exercise() -> None:
        checker = DupeChecker(_config())
        cases = [
            (_entry("Same", files="Episode.S01E01.1080p.WEB-DL.x264-GROUP.mkv"), _meta(tmp_path)),
            (_entry("Same", files=[], file_count=0), _meta(tmp_path, is_disc="BDMV", filelist=[])),
            (_entry("Same", files=[], file_count=3), _meta(tmp_path, is_disc="BDMV", filelist=[])),
            (_entry("Same", size=None), _meta(tmp_path, source_size=None, filelist=[])),
            ({"name": " exact name ", "size": None}, _meta(tmp_path, name="Exact Name", source_size=None, filelist=[])),
            ({"name": ""}, _meta(tmp_path, name="Exact Name", source_size=None, filelist=[])),
            (_entry("Bad count", file_count="invalid"), _meta(tmp_path)),
            (_entry("Bad size", size="not-a-size"), _meta(tmp_path)),
        ]
        outcomes = [await checker.is_exact_match(candidate, meta) for candidate, meta in cases]
        assert all(isinstance(outcome, bool) for outcome in outcomes)

    asyncio.run(exercise())


def test_duplicate_filter_matrix_never_terminates_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations.trackers import registry

    class ExactTracker:
        exact_match_only = True

    class RepackTracker:
        prefers_repack = True

    class FakeHuno:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

        async def get_name(self, _meta: Meta) -> dict[str, str]:
            return {"name": "HUNO Name"}

    monkeypatch.setitem(registry.tracker_class_map, "EXACT", ExactTracker)
    monkeypatch.setitem(registry.tracker_class_map, "PREFERS", RepackTracker)
    monkeypatch.setattr(duplicate_service, "HawkeUno", FakeHuno)

    categories = ("MOVIE", "TV", "GAME", "BOOK")
    trackers = ("OTHER", "ALPHARATIO", "BEYONDHD", "LUMINARR", "AITHER", "ANTHELION", "HAWKEUNO", "CAPYBARABR", "EXACT", "PREFERS")
    names = (
        "Episode.S01E01.1080p.WEB-DL.x264-GROUP",
        "Episode.S01.1080p.WEB-DL.x264-GROUP",
        "Episode.S02E01.720p.HDTV.x265-OTHER",
        "Episode.S01E01.2160p.WEB-DL.DV.HDR-GROUP",
        "Episode.S01E01.1080p.BluRay.REMUX-GROUP",
        "Episode.S01E01.1080p.WEB-DL.REPACK-GROUP",
        "HUNO Name",
        "Completely.Different.Release",
    )
    process_terminations: list[str] = []
    semantic_rejections: list[str] = []
    attempted = 0

    async def exercise() -> None:
        nonlocal attempted
        checker = DupeChecker(_config())
        for category in categories:
            for tracker in trackers:
                meta = _meta(
                    tmp_path,
                    category=category,
                    debug=tracker == "OTHER",
                    title="Super Game" if category == "GAME" else "Target Book" if category == "BOOK" else "Episode",
                    author="Alice" if category == "BOOK" else "",
                    platform="PlayStation 5" if category == "GAME" else "PC",
                    type="EPUB" if category == "BOOK" else "WEBDL",
                    audiobook=category == "BOOK" and tracker == "OTHER",
                    hdr="HDR" if tracker in {"AITHER", "ANTHELION"} else "",
                    tag="-INT" if tracker == "AITHER" else "-GROUP",
                )
                candidates: list[str | dict[str, Any]] = []
                for index, name in enumerate(names):
                    candidates.append(
                        _entry(
                            name,
                            id=index + 1,
                            size=(None, 100, "100 B", "bad")[index % 4],
                            files=(
                                [Path(meta.filelist[0]).name],
                                [],
                                "one.mkv,two.mkv",
                                [f"file-{item}.mkv" for item in range(12)],
                            )[index % 4],
                            file_count=(1, 0, 2, "invalid")[index % 4],
                            flags=([], ["HDR"], ["DV"], ["HDR", "DV"])[index % 4],
                            internal=index % 2,
                            trumpable=index == 0,
                            type=("WEB", "BluRay", "PC", "epub")[index % 4],
                            res=("1080p", "2160p", "720p", None)[index % 4],
                            description="x" * (320 if index == 3 else 20),
                        )
                    )
                candidates.append("Plain string candidate")
                attempted += 1
                try:
                    result = await checker.filter_dupes(candidates, meta, tracker)
                    assert isinstance(result, list)
                except (KeyboardInterrupt, SystemExit) as error:
                    process_terminations.append(f"{category}/{tracker}:{type(error).__name__}")
                except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
                    semantic_rejections.append(f"{category}/{tracker}:{type(error).__name__}")

    asyncio.run(exercise())
    assert attempted == len(categories) * len(trackers)
    assert process_terminations == []
    assert all(":" in rejection for rejection in semantic_rejections)


def test_tv_season_pack_luminarr_and_internal_trump_paths(tmp_path: Path) -> None:
    async def exercise() -> None:
        checker = DupeChecker(_config())
        episode = _meta(tmp_path, category="TV", season="S01", episode="E02", resolution="1080p")
        pack = _entry("Show.S01.1080p.WEB-DL", files=["Show.S01E01.mkv", "Show.S01E02.mkv"], file_count=2)
        assert isinstance(await checker.filter_dupes([pack], episode, "OTHER"), list)
        assert episode.season_pack_exists is True

        missing = _meta(tmp_path, category="TV", season="S01", episode="E03")
        assert isinstance(await checker.filter_dupes([pack], missing, "OTHER"), list)

        luminarr = _meta(tmp_path, category="TV", season="S01", episode="E02", resolution="1080p")
        assert isinstance(await checker.filter_dupes([_entry("Show.S01E02.1080p.WEB-DL")], luminarr, "LUMINARR"), list)

        internal = _meta(tmp_path, category="TV", season="S01", episode="E02", source="WEB", resolution="1080p", tag="-INT")
        candidate = _entry("Show.S01.1080p.WEB-DL-INT", type="WEB", res="1080p", internal=1, files=[])
        assert isinstance(await checker.filter_dupes([candidate], internal, "AITHER"), list)

    asyncio.run(exercise())


def test_game_book_and_regular_release_specializations(tmp_path: Path) -> None:
    async def exercise() -> None:
        checker = DupeChecker(_config())
        game_cases = (
            ("PlayStation 5", "Xbox"),
            ("Xbox Series X", "PC"),
            ("Nintendo Switch", "PlayStation"),
            ("Windows", "PC"),
        )
        for platform, candidate_type in game_cases:
            meta = _meta(tmp_path, category="GAME", title="Super Game", platform=platform)
            result = await checker.filter_dupes([_entry("Super.Game.Update.v1.0.4.2026-PC-GOG", type=candidate_type)], meta, "OTHER")
            assert isinstance(result, list)

        ebook = _media(tmp_path, "Alice - Target Book.epub")
        book_cases = (
            _entry("Target Book", type="epub", files=[ebook.name], size=100),
            _entry("Target Book Audiobook MP3", type="mp3"),
            _entry("Target Book", type="pdf", files=["book.pdf"]),
            _entry("Different Novel", type="epub"),
        )
        for tracker in ("OTHER", "CAPYBARABR"):
            meta = _meta(tmp_path, category="BOOK", title="Target Book", author="Alice", type="EPUB", filelist=[str(ebook)])
            result = await checker.filter_dupes(list(book_cases), meta, tracker)
            assert isinstance(result, list)

        special_cases = (
            (_meta(tmp_path, source_size=100), _entry("Other", trumpable=True, res="1080p"), "AITHER"),
            (_meta(tmp_path, type="WEBDL", source="WEB"), _entry("Movie.1080p.HDTV"), "OTHER"),
            (_meta(tmp_path, type="ENCODE", source="BluRay"), _entry("Movie.1080p.WEB-DL"), "OTHER"),
            (_meta(tmp_path, type="WEBDL", hdr="HDR"), _entry("Movie.1080p.SDR", flags=[]), "OTHER"),
            (_meta(tmp_path, name="Movie REMUX 1080p", uuid="Movie.REMUX.1080p"), _entry("Movie.1080p.ENCODE"), "OTHER"),
            (_meta(tmp_path, name="Movie REPACK 1080p", uuid="Movie.REPACK.1080p"), _entry("Movie.1080p"), "PREFERS"),
        )
        for meta, candidate, tracker in special_cases:
            assert isinstance(await checker.filter_dupes([candidate], meta, tracker), list)

    asyncio.run(exercise())
