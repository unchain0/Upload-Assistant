"""Regression coverage for release-marker detection in edition handling."""

import asyncio

import pytest

from src.domain_models.release import Meta
from src.services.edition_identity_service import (
    _has_release_token,
    _strip_release_tokens,
    get_edition,
)


@pytest.mark.parametrize(
    ("value", "token"),
    [
        ("Show.V2.1080p", "V2"),
        ("Show-V3-1080p", "V3"),
        ("Show V4 1080p", "V4"),
        ("Show.REPACK.1080p", "REPACK"),
        ("Show-REPACK2-1080p", "REPACK2"),
        ("Show REPACK3 1080p", "REPACK3"),
        ("Show.PROPER.1080p", "PROPER"),
        ("Show-PROPER2-1080p", "PROPER2"),
        ("Show PROPER3 1080p", "PROPER3"),
        ("Show.RERIP.1080p", "RERIP"),
    ],
)
def test_release_marker_matches_standalone_tokens(
    value: str, token: str
) -> None:
    assert _has_release_token(value, token)


@pytest.mark.parametrize(
    ("value", "token"),
    [
        ("TV2", "V2"),
        ("TV3", "V3"),
        ("TV4", "V4"),
        ("REPACKAGED", "REPACK"),
        ("REPACK2X", "REPACK2"),
        ("REPACK3X", "REPACK3"),
        ("PROPERLY", "PROPER"),
        ("PROPER2X", "PROPER2"),
        ("PROPER3X", "PROPER3"),
        ("RERIPPED", "RERIP"),
    ],
)
def test_release_marker_does_not_match_inside_other_tokens(
    value: str, token: str
) -> None:
    assert not _has_release_token(value, token)


def test_strip_release_tokens_uses_non_alphanumeric_boundaries() -> None:
    assert _strip_release_tokens("Director_PROPER2") == "Director_"


def test_get_edition_detects_repack_between_hyphens() -> None:
    edition, repack, hybrid = asyncio.run(
        get_edition("Movie-REPACK-1080p", None, [], "", Meta(category="TV"))
    )

    assert edition == ""
    assert repack == "REPACK"
    assert not hybrid


def _edition_meta(
    *, duration: object = "7200", unattended: bool = True
) -> Meta:
    return Meta(
        category="MOVIE",
        anime=False,
        unattended=unattended,
        unattended_confirm=not unattended,
        mediainfo={
            "media": {"track": [{"@type": "General", "Duration": duration}]}
        },
        imdb_info={
            "edition_count": 3,
            "edition_details": {
                "director": {
                    "seconds": 7200,
                    "minutes": 120,
                    "attributes": ["director's cut"],
                    "display_name": "Director",
                },
                "extended": {
                    "seconds": 7220,
                    "minutes": 120,
                    "attributes": ["extended edition"],
                    "display_name": "Extended",
                },
                "theatrical": {
                    "seconds": 7190,
                    "minutes": 120,
                    "attributes": [],
                    "display_name": "Theatrical",
                },
            },
        },
    )


def test_get_edition_selects_duration_match_in_unattended_mode() -> None:
    edition, repack, hybrid = asyncio.run(
        get_edition(
            "Movie.2024.1080p-GRP", None, ["movie.mkv"], "", _edition_meta()
        )
    )

    assert edition in {"Director's Cut", "Extended"}
    assert repack == ""
    assert not hybrid


def test_get_edition_interactive_selection_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    async def select_second(*_args: object, **_kwargs: object) -> str:
        return "2"

    monkeypatch.setattr(edition_service, "prompt_in_thread", select_second)
    edition, _, _ = asyncio.run(
        get_edition(
            "Movie", None, ["movie.mkv"], "", _edition_meta(unattended=False)
        )
    )
    assert edition == "Extended"

    async def invalid(*_args: object, **_kwargs: object) -> str:
        return "99"

    monkeypatch.setattr(edition_service, "prompt_in_thread", invalid)
    edition, _, _ = asyncio.run(
        get_edition(
            "Movie", None, ["movie.mkv"], "", _edition_meta(unattended=False)
        )
    )
    assert edition in {"Director's Cut", "Extended"}

    async def failed(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("prompt failed")

    monkeypatch.setattr(edition_service, "prompt_in_thread", failed)
    edition, _, _ = asyncio.run(
        get_edition(
            "Movie", None, ["movie.mkv"], "", _edition_meta(unattended=False)
        )
    )
    assert edition in {"Director's Cut", "Extended"}


def test_get_edition_handles_invalid_and_unmatched_durations() -> None:
    invalid = _edition_meta(duration="invalid")
    assert (
        asyncio.run(get_edition("Movie", None, ["movie.mkv"], "", invalid))[0]
        == ""
    )

    unmatched = _edition_meta(duration="3600")
    assert (
        asyncio.run(get_edition("Movie", None, ["movie.mkv"], "", unmatched))[
            0
        ]
        == ""
    )


def test_get_edition_matches_bluray_playlists() -> None:
    meta = _edition_meta()
    meta.is_disc = "BDMV"
    meta.mediainfo = {}
    meta.discs = [
        {
            "playlists": [
                {
                    "file": "00001.MPLS",
                    "edition": "Director's Cut",
                    "duration": 7200,
                },
                {
                    "file": "00002.MPLS",
                    "edition": "Theatrical",
                    "duration": 7190,
                },
            ],
            "all_valid_playlists": [
                {
                    "file": "00001.MPLS",
                    "edition": "Director's Cut",
                    "duration": 7200,
                },
                {
                    "file": "00002.MPLS",
                    "edition": "Theatrical",
                    "duration": 7190,
                },
            ],
        }
    ]

    edition, _, _ = asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", meta)
    )
    assert edition


def test_get_edition_bluray_interactive_playlist_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    meta = _edition_meta(unattended=False)
    meta.is_disc = "BDMV"
    meta.mediainfo = {}
    meta.discs = [
        {
            "playlists": [
                {
                    "file": "00001.MPLS",
                    "edition": "Custom Cut",
                    "duration": 7210,
                }
            ]
        }
    ]

    async def playlist_edition(*_args: object, **_kwargs: object) -> str:
        return "e"

    monkeypatch.setattr(edition_service, "prompt_in_thread", playlist_edition)
    edition, _, _ = asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", meta)
    )
    assert "Custom" in edition

    meta.discs = [
        {
            "playlists": [
                {"file": "00001.MPLS", "edition": "", "duration": 7210}
            ]
        }
    ]
    edition, _, _ = asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", meta)
    )
    assert edition


def test_get_edition_guessing_manual_override_repack_and_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    def guessed_edition(
        _value: str, _options: dict[str, object] | None = None
    ) -> dict[str, list[str]]:
        return {"release_group": ["GRP"], "edition": ["limited", "edition"]}

    async def criterion_distributor(_value: str) -> str:
        return "Criterion"

    monkeypatch.setattr(edition_service, "guessit_fn", guessed_edition)
    monkeypatch.setattr(
        edition_service, "get_distributor", criterion_distributor
    )

    meta = Meta(category="TV", webdv=False, is_disc="BDMV")
    edition, repack, hybrid = asyncio.run(
        get_edition(
            "Movie.OPEN.MATTE.HYBRID.REPACK2-GRP.mkv",
            {"label": "Movie.Limited.Edition"},
            ["movie.mkv"],
            "",
            meta,
        )
    )
    assert repack == "REPACK2"
    assert hybrid
    assert "limited" not in edition.casefold()

    manual, repack, _ = asyncio.run(
        get_edition(
            "Movie.PROPER2",
            None,
            [],
            ["Collector's", "Edition"],
            Meta(category="TV"),
        )
    )
    assert manual == "Collector's Edition"
    assert repack == "PROPER2"

    _, repack, _ = asyncio.run(
        get_edition("Movie.RERIP", None, [], "", Meta(category="TV"))
    )
    assert repack == "RERIP"


def test_duration_and_smart_title_helpers() -> None:
    from src.services.edition_identity_service import (
        format_duration,
        smart_title,
    )

    assert format_duration(3661) == "1:01:01"
    assert smart_title("director's cut") == "Director's Cut"


def _bluray_meta(
    *,
    unattended: bool = False,
    playlist_edition: str = "Custom Cut",
    duration: float = 7200,
) -> Meta:
    meta = _edition_meta(unattended=unattended)
    meta.is_disc = "BDMV"
    meta.mediainfo = {}
    playlists = [
        {
            "file": "00001.MPLS",
            "edition": playlist_edition,
            "duration": duration,
        }
    ]
    meta.discs = [{"playlists": playlists, "all_valid_playlists": playlists}]
    return meta


def test_invalid_edition_count_and_single_duration_match() -> None:
    invalid_count = Meta(
        category="TV",
        imdb_info={"edition_count": object(), "edition_details": {}},
    )
    assert (
        asyncio.run(get_edition("Movie", None, [], "", invalid_count))[0] == ""
    )

    meta = _edition_meta(duration="7200")
    meta.imdb_info = {
        "edition_count": 2,
        "edition_details": {
            "director": {
                "seconds": 7200,
                "minutes": 120,
                "attributes": ["director's cut"],
                "display_name": "Director",
            },
            "far": {
                "seconds": 9000,
                "minutes": 150,
                "attributes": ["extended"],
                "display_name": "Far",
            },
        },
    }
    assert (
        asyncio.run(get_edition("Movie", None, ["movie.mkv"], "", meta))[0]
        == "Director's Cut"
    )


def test_bluray_numeric_default_and_prompt_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    async def choose_first(*_args: object, **_kwargs: object) -> str:
        return "1"

    monkeypatch.setattr(edition_service, "prompt_in_thread", choose_first)
    assert asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", _bluray_meta())
    )[0]

    async def choose_default(*_args: object, **_kwargs: object) -> str:
        return ""

    monkeypatch.setattr(edition_service, "prompt_in_thread", choose_default)
    assert asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", _bluray_meta())
    )[0]

    async def fail(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("prompt failed")

    monkeypatch.setattr(edition_service, "prompt_in_thread", fail)
    assert asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", _bluray_meta())
    )[0]


def test_bluray_playlist_theatrical_attribute_duplicate_and_mixed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    async def use_playlist(*_args: object, **_kwargs: object) -> str:
        return "e"

    monkeypatch.setattr(edition_service, "prompt_in_thread", use_playlist)

    theatrical = _bluray_meta(playlist_edition="Theatrical")
    assert (
        asyncio.run(
            get_edition("BDMV", {"label": "Movie"}, [], "", theatrical)
        )[0]
        == ""
    )

    duplicate = _bluray_meta(playlist_edition="Custom Cut")
    duplicate.discs = [
        {
            "playlists": [
                {
                    "file": "00001.MPLS",
                    "edition": "Custom Cut",
                    "duration": 7200,
                },
                {
                    "file": "00002.MPLS",
                    "edition": "Custom Cut",
                    "duration": 7200,
                },
            ]
        }
    ]
    assert (
        "Custom"
        in asyncio.run(
            get_edition("BDMV", {"label": "Movie"}, [], "", duplicate)
        )[0]
    )

    mixed = _bluray_meta()
    mixed.imdb_info = {
        "edition_count": 2,
        "edition_details": {
            "attribute": {
                "seconds": 7200,
                "minutes": 120,
                "attributes": ["director's cut"],
                "display_name": "Director",
            },
            "theatrical": {
                "seconds": 7300,
                "minutes": 122,
                "attributes": [],
                "display_name": "Theatrical",
            },
        },
    }
    mixed.discs = [
        {
            "playlists": [
                {
                    "file": "00001.MPLS",
                    "edition": "Director's Cut",
                    "duration": 7200,
                },
                {
                    "file": "00002.MPLS",
                    "edition": "Theatrical",
                    "duration": 7300,
                },
            ]
        }
    ]
    result = asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", mixed)
    )[0]
    assert "Theatrical" in result and "Director's Cut" in result


def test_bluray_unattended_and_nonmatching_playlist_paths() -> None:
    unattended = _bluray_meta(unattended=True)
    assert asyncio.run(
        get_edition("BDMV", {"label": "Movie"}, [], "", unattended)
    )[0]

    no_match = _bluray_meta(unattended=True, duration=3000)
    assert (
        asyncio.run(get_edition("BDMV", {"label": "Movie"}, [], "", no_match))[
            0
        ]
        == ""
    )

    one_no_attributes = _bluray_meta(unattended=True)
    one_no_attributes.imdb_info = {
        "edition_count": 2,
        "edition_details": {
            "theatrical": {
                "seconds": 7200,
                "minutes": 120,
                "attributes": [],
                "display_name": "Theatrical",
            },
            "far": {
                "seconds": 9000,
                "minutes": 150,
                "attributes": ["extended"],
                "display_name": "Far",
            },
        },
    }
    assert (
        asyncio.run(
            get_edition("BDMV", {"label": "Movie"}, [], "", one_no_attributes)
        )[0]
        == ""
    )


def test_guessing_error_cleanup_and_all_repack_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    class BrokenGuess(dict):
        calls = 0

        def get(self, key, default=None):
            self.calls += 1
            if key == "edition":
                raise RuntimeError("broken edition")
            return super().get(key, default)

    monkeypatch.setattr(
        edition_service,
        "guessit_fn",
        lambda *_args, **_kwargs: BrokenGuess(release_group="GRP"),
    )
    assert (
        asyncio.run(
            get_edition(
                "dc.Movie-GRP", {}, ["movie.mkv"], "", Meta(category="TV")
            )
        )[0]
        == ""
    )
    assert (
        asyncio.run(
            get_edition(
                "Movie-GRP", None, ["movie.mkv"], "", Meta(category="TV")
            )
        )[0]
        == ""
    )

    async def distributor(_value: str) -> str:
        return "Criterion"

    monkeypatch.setattr(edition_service, "get_distributor", distributor)
    edition, _, hybrid = asyncio.run(
        get_edition(
            "Movie",
            None,
            [],
            "Criterion, Remastered  Director's Cut",
            Meta(category="TV", is_disc="BDMV", webdv=True),
        )
    )
    assert edition == "Director's Cut"
    assert hybrid is True

    for marker in ("REPACK3", "PROPER", "PROPER3"):
        _, repack, _ = asyncio.run(
            get_edition(
                f"Movie.{marker}.1080p", None, [], "", Meta(category="TV")
            )
        )
        assert repack == marker


def test_bluray_empty_numeric_and_failure_choose_theatrical_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    def no_attribute_closest(playlist_edition: str = "") -> Meta:
        meta = _bluray_meta(playlist_edition=playlist_edition, duration=7200)
        meta.imdb_info = {
            "edition_count": 2,
            "edition_details": {
                "theatrical": {
                    "seconds": 7200,
                    "minutes": 120,
                    "attributes": [],
                    "display_name": "Theatrical",
                },
                "director": {
                    "seconds": 7220,
                    "minutes": 120,
                    "attributes": ["director's cut"],
                    "display_name": "Director",
                },
            },
        }
        return meta

    async def use_playlist(*_args: object, **_kwargs: object) -> str:
        return "e"

    monkeypatch.setattr(edition_service, "prompt_in_thread", use_playlist)
    assert (
        asyncio.run(
            get_edition(
                "BDMV", {"label": "Movie"}, [], "", no_attribute_closest()
            )
        )[0]
        == ""
    )

    async def select_theatrical(*_args: object, **_kwargs: object) -> str:
        return "1"

    monkeypatch.setattr(edition_service, "prompt_in_thread", select_theatrical)
    assert (
        asyncio.run(
            get_edition(
                "BDMV",
                {"label": "Movie"},
                [],
                "",
                no_attribute_closest("Custom"),
            )
        )[0]
        == ""
    )

    async def fail(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("prompt failed")

    monkeypatch.setattr(edition_service, "prompt_in_thread", fail)
    assert (
        asyncio.run(
            get_edition(
                "BDMV",
                {"label": "Movie"},
                [],
                "",
                no_attribute_closest("Custom"),
            )
        )[0]
        == ""
    )


def test_short_playlist_edition_is_discarded_and_spacing_is_compacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.services.edition_identity_service as edition_service

    async def use_playlist(*_args: object, **_kwargs: object) -> str:
        return "e"

    monkeypatch.setattr(edition_service, "prompt_in_thread", use_playlist)
    short = _bluray_meta(playlist_edition="Cut")
    assert (
        asyncio.run(get_edition("BDMV", {"label": "Movie"}, [], "", short))[0]
        == ""
    )

    async def no_distributor(_value: str) -> str:
        return ""

    monkeypatch.setattr(edition_service, "get_distributor", no_distributor)
    edition, _, _ = asyncio.run(
        get_edition(
            "Movie", None, [], "Director  Remastered  Cut", Meta(category="TV")
        )
    )
    assert edition == "Director Cut"
