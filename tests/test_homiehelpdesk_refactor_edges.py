from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk


def _tracker() -> HomieHelpDesk:
    return HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})


def test_homiehelpdesk_discogs_disabled() -> None:
    meta = Meta(category="MUSIC", music_discogs_enabled=False)
    assert _tracker()._discogs_reference(meta) == ""


def test_homiehelpdesk_additional_data_non_music() -> None:
    result = asyncio.run(
        _tracker().get_additional_data(Meta(category="MOVIE"))
    )
    assert result == {}


def test_homiehelpdesk_rejects_dvdrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.homiehelpdesk.logger.info",
        lambda *_args, **_kwargs: None,
    )
    meta = Meta(category="MOVIE", type="DVDRIP")
    assert not asyncio.run(_tracker().get_additional_checks(meta))


def test_homiehelpdesk_accepts_regular_upload() -> None:
    meta = Meta(category="MOVIE", type="REMUX")
    assert asyncio.run(_tracker().get_additional_checks(meta))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    (
        ({"audiobook": True}, "AUDIOBOOK"),
        ({"comic": True}, "COMICS"),
        ({"manga": True}, "MANGA"),
        ({"magazine": True}, "MAGAZINE"),
        ({}, "BOOKS"),
    ),
)
def test_homiehelpdesk_book_category_variants(
    kwargs: dict[str, bool],
    expected: str,
) -> None:
    meta = Meta(category="BOOK", **kwargs)
    assert _tracker()._book_category(meta) == expected


@pytest.mark.parametrize(
    ("meta", "explicit", "expected"),
    (
        (Meta(category="BOOK", audiobook=True), "", "AUDIOBOOK"),
        (Meta(category="MOVIE"), "TV", "TV"),
        (Meta(category="MOVIE"), "", "MOVIE"),
    ),
)
def test_homiehelpdesk_resolved_category(
    meta: Meta,
    explicit: str,
    expected: str,
) -> None:
    assert _tracker()._resolved_category(meta, explicit) == expected


def test_homiehelpdesk_category_mapping_only() -> None:
    result = asyncio.run(_tracker().get_category_id(Meta(), mapping_only=True))
    assert result["AUDIOBOOK"] == "8"


def test_homiehelpdesk_category_mapping_reverse() -> None:
    result = asyncio.run(_tracker().get_category_id(Meta(), reverse=True))
    assert result["1"] == "MOVIE"


@pytest.mark.parametrize(
    ("meta", "explicit", "expected"),
    (
        (Meta(category="BOOK", audiobook=True), "", "8"),
        (Meta(category="MOVIE"), "TV", "2"),
        (Meta(category="UNKNOWN"), "", "0"),
    ),
)
def test_homiehelpdesk_category_id_selection(
    meta: Meta,
    explicit: str,
    expected: str,
) -> None:
    result = asyncio.run(_tracker().get_category_id(meta, category=explicit))
    assert result == {"category_id": expected}


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (None, ""),
        ("remux", "REMUX"),
    ),
)
def test_homiehelpdesk_normalized_type(value: object, expected: str) -> None:
    assert _tracker()._normalized_type(value) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (
            Meta(
                category="MUSIC",
                format="AAC",
                music_release={"fields": {"format": {"value": "FLAC"}}},
            ),
            "FLAC",
        ),
        (
            Meta(
                category="MUSIC",
                format="AAC",
                music_release={"fields": {"format": "bad"}},
            ),
            "AAC",
        ),
        (
            Meta(category="MUSIC", format="AAC", music_release={"fields": []}),
            "AAC",
        ),
    ),
)
def test_homiehelpdesk_music_type_variants(meta: Meta, expected: str) -> None:
    assert _tracker()._music_type(meta) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (Meta(category="GAME", console_game=True), "CONSOLE"),
        (
            Meta(category="GAME", console_game=False, platform="windows"),
            "WINDOWS",
        ),
    ),
)
def test_homiehelpdesk_game_type(meta: Meta, expected: str) -> None:
    assert _tracker()._game_type(meta) == expected


@pytest.mark.parametrize(
    ("resolved", "expected"),
    (("EPUB", "EPUB"), ("TXT", "OTHER")),
)
def test_homiehelpdesk_book_type(resolved: str, expected: str) -> None:
    mapping = _tracker()._type_mapping()
    assert _tracker()._book_type(resolved, mapping) == expected


@pytest.mark.parametrize(
    ("meta", "explicit", "expected"),
    (
        (Meta(category="GAME", console_game=True), "", "CONSOLE"),
        (Meta(category="GAME", platform="windows"), "", "WINDOWS"),
        (Meta(category="MUSIC", format="AAC"), "", "AAC"),
        (Meta(category="BOOK", type="TXT"), "", "OTHER"),
        (Meta(category="MOVIE", type="REMUX"), "", "REMUX"),
        (Meta(category="MOVIE", type="REMUX"), "WEBDL", "WEBDL"),
    ),
)
def test_homiehelpdesk_resolved_type(
    meta: Meta,
    explicit: str,
    expected: str,
) -> None:
    mapping = _tracker()._type_mapping()
    assert _tracker()._resolved_type(meta, explicit, mapping) == expected


def test_homiehelpdesk_type_mapping_only() -> None:
    result = asyncio.run(_tracker().get_type_id(Meta(), mapping_only=True))
    assert result["OTHER"] == "23"


def test_homiehelpdesk_type_mapping_reverse() -> None:
    result = asyncio.run(_tracker().get_type_id(Meta(), reverse=True))
    assert result["28"] == "CONSOLE"


@pytest.mark.parametrize(
    ("meta", "explicit", "expected"),
    (
        (Meta(category="GAME", console_game=True), "", "28"),
        (Meta(category="GAME", platform="PC"), "", "25"),
        (Meta(category="MUSIC", format="AAC"), "", "8"),
        (Meta(category="BOOK", type="TXT"), "", "23"),
        (Meta(category="MOVIE", type="UNKNOWN"), "", "0"),
        (Meta(category="MOVIE", type="REMUX"), "ENCODE", "3"),
    ),
)
def test_homiehelpdesk_type_id_selection(
    meta: Meta,
    explicit: str,
    expected: str,
) -> None:
    result = asyncio.run(_tracker().get_type_id(meta, type=explicit))
    assert result == {"type_id": expected}


def test_homiehelpdesk_resolution_mapping_only() -> None:
    result = asyncio.run(
        _tracker().get_resolution_id(Meta(), mapping_only=True)
    )
    assert result["2160p"] == "2"


def test_homiehelpdesk_resolution_mapping_reverse() -> None:
    result = asyncio.run(_tracker().get_resolution_id(Meta(), reverse=True))
    assert result["10"] == "Other"


@pytest.mark.parametrize(
    ("meta", "explicit", "expected"),
    (
        (Meta(resolution="1080p"), None, "3"),
        (Meta(resolution="1080p"), "2160p", "2"),
        (Meta(resolution="1080p"), "480i", "9"),
        (Meta(resolution="UNKNOWN"), None, "10"),
    ),
)
def test_homiehelpdesk_resolution_id_selection(
    meta: Meta,
    explicit: str | None,
    expected: str,
) -> None:
    result = asyncio.run(
        _tracker().get_resolution_id(meta, resolution=explicit)
    )
    assert result == {"resolution_id": expected}


def test_homiehelpdesk_invalid_music_release() -> None:
    assert _tracker()._music_release(Meta(music_release="bad")) == {}


def test_homiehelpdesk_invalid_external_ids() -> None:
    meta = Meta(music_release={"external_ids": "bad"})
    assert _tracker()._music_external_ids(meta) == {}


def test_homiehelpdesk_musicbrainz_release_group_fallback() -> None:
    value = "12345678-1234-1234-1234-123456789abc"
    meta = Meta(
        music_release={"external_ids": {"musicbrainz_release_group": value}}
    )
    assert _tracker()._musicbrainz_reference(meta) == value


@pytest.mark.parametrize(
    ("values", "expected"),
    (
        ((None, " ", " value "), "value"),
        ((None, ""), ""),
    ),
)
def test_homiehelpdesk_first_reference(
    values: tuple[object, ...],
    expected: str,
) -> None:
    assert _tracker()._first_reference(values) == expected
