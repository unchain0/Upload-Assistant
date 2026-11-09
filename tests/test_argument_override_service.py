from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from src.domain_models.release import Meta
from src.services import argument_override_service as override_module
from src.services.argument_override_service import ApplyOverrides


class _Parser:
    def __init__(self, updates: dict[str, Any], *, fail: bool = False) -> None:
        self.updates = updates
        self.fail = fail
        self.calls: list[tuple[list[str], Meta]] = []

    def parse(
        self, argv: list[str], meta: Meta
    ) -> tuple[Meta, object, list[str]]:
        self.calls.append((argv, meta.copy()))
        if self.fail:
            raise RuntimeError("parse failed")
        meta.update(self.updates)
        return meta, object(), []


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(tmp_path / "release.mkv"),
        "category": "MOVIE",
        "tmdb_id": 123,
        "imdb_id": 456,
        "tvdb_id": 789,
        "title": "Original",
        "debug": True,
    }
    state.update(values)
    return Meta(state)


def _write_args(tmp_path: Path, payload: object) -> Path:
    target = tmp_path / "data" / "templates" / "user-args.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_parse_tmdb_id_covers_empty_prefixed_numeric_and_invalid_values() -> (
    None
):
    service = ApplyOverrides({})

    async def exercise() -> None:
        assert await service.parse_tmdb_id(None, "MOVIE") == ("MOVIE", 0)
        assert await service.parse_tmdb_id("  ", "TV") == ("TV", 0)
        assert await service.parse_tmdb_id("tv/123", None) == ("TV", 123)
        assert await service.parse_tmdb_id("movie/456/extra", None) == (
            "MOVIE",
            456,
        )
        assert await service.parse_tmdb_id("person/789", "MOVIE") == (
            "MOVIE",
            789,
        )
        assert await service.parse_tmdb_id("movie/not-a-number", None) == (
            "MOVIE",
            0,
        )
        assert await service.parse_tmdb_id("321", "TV") == ("TV", 321)
        assert await service.parse_tmdb_id("invalid", "TV") == ("TV", 0)

    asyncio.run(exercise())


def test_get_source_override_applies_matching_tmdb_and_skips_invalid_entries(
    tmp_path: Path,
) -> None:
    _write_args(
        tmp_path,
        {
            "entries": [
                {"tmdb_id": None, "args": ["--title", "ignored"]},
                {"tmdb_id": "tv/123", "args": ["--title", "wrong-category"]},
                {"tmdb_id": "movie/999", "args": ["--title", "wrong-id"]},
                {"tmdb_id": "movie/123", "args": ["--title", "Matched"]},
            ]
        },
    )
    parser = _Parser({"title": "Matched"})
    service = ApplyOverrides({}, lambda _config: parser)
    meta = _meta(tmp_path, tmdb_id="123")

    result = asyncio.run(service.get_source_override(meta))

    assert result.title == "Matched"
    assert parser.calls[0][0] == ["upload.py", "--title", "Matched"]
    assert result.path == str(tmp_path / "release.mkv")


def test_get_source_override_applies_tvdb_and_imdb_matches(
    tmp_path: Path,
) -> None:
    parser = _Parser({"title": "Matched"})
    service = ApplyOverrides({}, lambda _config: parser)

    _write_args(
        tmp_path,
        {
            "other_ids": [
                {"tvdb_id": 789, "args": ["--title", "TVDB"]},
                {"imdb_id": "tt456", "args": ["--title", "IMDB"]},
            ]
        },
    )
    tvdb = asyncio.run(
        service.get_source_override(
            _meta(tmp_path, tvdb_id="789"), other_id=True
        )
    )
    assert tvdb.title == "Matched"
    assert parser.calls[-1][0][-1] == "TVDB"

    _write_args(
        tmp_path,
        {
            "other_ids": [
                {"tvdb_id": 0, "args": ["--title", "ignored"]},
                {"imdb_id": "tt456", "args": ["--title", "IMDB"]},
            ]
        },
    )
    imdb = asyncio.run(
        service.get_source_override(
            _meta(tmp_path, imdb_id="456", tvdb_id=0), other_id=True
        )
    )
    assert imdb.title == "Matched"
    assert parser.calls[-1][0][-1] == "IMDB"

    _write_args(
        tmp_path,
        {"other_ids": [{"imdb_id": 0, "args": ["--title", "ignored"]}]},
    )
    unchanged = _meta(tmp_path, imdb_id=0, tvdb_id=0)
    assert (
        asyncio.run(
            service.get_source_override(unchanged, other_id=True)
        ).title
        == "Original"
    )


def test_get_source_override_handles_missing_and_invalid_json(
    tmp_path: Path,
) -> None:
    service = ApplyOverrides({})
    meta = _meta(tmp_path)
    assert asyncio.run(service.get_source_override(meta)) is meta

    target = _write_args(tmp_path, {})
    target.write_text("not-json", encoding="utf-8")
    assert asyncio.run(service.get_source_override(meta)) is meta


def test_apply_args_without_parser_and_regular_changes(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    no_parser = ApplyOverrides({})
    assert (
        asyncio.run(no_parser.apply_args_to_meta(meta, ["--title", "ignored"]))
        is meta
    )
    assert meta.title == "Original"

    parser = _Parser(
        {"title": "Updated", "path": "/wrong", "untouched": "new"}
    )
    service = ApplyOverrides({}, lambda _config: parser)
    result = asyncio.run(
        service.apply_args_to_meta(
            meta, ["--title", "Updated", "--path", "/wrong", "--flag"]
        )
    )
    assert result.title == "Updated"
    assert result.path == str(tmp_path / "release.mkv")
    assert result.get("untouched") is False


def test_apply_args_updates_all_identifier_aliases_and_value_shapes(
    tmp_path: Path,
) -> None:
    parser = _Parser({})
    service = ApplyOverrides({}, lambda _config: parser)
    meta = _meta(tmp_path)

    result = asyncio.run(
        service.apply_args_to_meta(
            meta,
            [
                "--tmdb",
                "101",
                "--tvmaze",
                "202",
                "--imdb",
                "tt303",
                "--tvdb",
                "not-numeric",
            ],
        )
    )

    assert (
        result.tmdb_id == 101
        and result.tmdb == 101
        and result.tmdb_manual == 101
    )
    assert (
        result.tvmaze_id == 202
        and result.tvmaze == 202
        and result.tvmaze_manual == 202
    )
    assert (
        result.imdb_id == 303
        and result.imdb == 303
        and result.imdb_manual == 303
    )
    assert (
        result.tvdb_id == "not-numeric" and result.tvdb_manual == "not-numeric"
    )

    invalid_imdb = asyncio.run(
        service.apply_args_to_meta(_meta(tmp_path), ["--imdb", "ttabc"])
    )
    assert invalid_imdb.imdb_id == "ttabc"


def test_refactor_helper_edges(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    assert override_module._first_tmdb_match([], meta, 123) is None
    assert override_module._normalized_imdb_entry({"imdb_id": 456}) == 456

    updated = meta.copy()
    modified: list[str] = []
    override_module._apply_regular_override(meta, updated, "title", modified)
    assert modified == []
    assert meta.title == "Original"


def test_apply_args_parser_failure_is_non_fatal(tmp_path: Path) -> None:
    parser = _Parser({}, fail=True)
    service = ApplyOverrides({}, lambda _config: parser)
    meta = _meta(tmp_path)
    assert (
        asyncio.run(service.apply_args_to_meta(meta, ["--title", "Updated"]))
        is meta
    )
    assert meta.title == "Original"
