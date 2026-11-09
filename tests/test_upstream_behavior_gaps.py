"""Behaviors whose upstream commits were not covered by an earlier test name."""

import asyncio
import json

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import (
    _expected_anime_season,
    _get_arm_mal_id,
    arm_mal_from_mappings,
    get_romaji,
)
from src.integrations.media.audio import (
    LossyDtsDuplicateError,
    dts_core_additional_check,
)
from src.integrations.media.screenshot_capture import run_ffmpeg
from src.services.media_source_service import get_source
from src.services.preparation_helpers import _bluray_lookup_allowed


def _mapping(mal: object = 123, season: object = 2, **extra: object) -> dict:
    payload = {
        "myanimelist": mal,
        "themoviedb-season": season,
        "thetvdb": 456,
        "imdb": "tt0000789",
    }
    payload.update(extra)
    return payload


def _tv_meta(tmp_path) -> Meta:
    return Meta(
        base_dir=str(tmp_path),
        category="TV",
        tmdb_id=345,
        tvdb_id=456,
        imdb_id=789,
    )


def test_arm_keeps_only_one_matching_tv_season(tmp_path) -> None:
    meta = _tv_meta(tmp_path)
    chosen = arm_mal_from_mappings(
        [_mapping(), _mapping(mal=234, season=1)], meta, 2
    )
    ambiguous = arm_mal_from_mappings(
        [_mapping(), _mapping(mal=234, season=2)], meta, 2
    )
    wrong_tvdb = arm_mal_from_mappings(
        [_mapping(thetvdb=999)], meta, 2
    )
    movie = Meta(base_dir=str(tmp_path), category="MOVIE", tmdb_id=345)

    assert chosen == 123
    assert ambiguous == 0
    assert wrong_tvdb == 0
    assert arm_mal_from_mappings([_mapping()], movie, 2) == 0


def test_anime_season_uses_explicit_evidence_only() -> None:
    manual = _expected_anime_season(
        Meta(manual_season="S03", filename="Astral.Journey.S02E01.mkv")
    )
    zero = _expected_anime_season(Meta(manual_season=0))
    ranged = _expected_anime_season(
        Meta(manual_season="1-3", filename="Astral.Journey.S02E01.mkv")
    )
    filename = _expected_anime_season(
        Meta(filename="Astral.Journey.S02E01.mkv")
    )
    episode_only = _expected_anime_season(
        Meta(filename="Astral.Journey.14.mkv")
    )

    assert manual == 3
    assert zero == 0
    assert ranged is None
    assert filename == 2
    assert episode_only is None


def test_arm_lookup_uses_the_cache_and_retries_errors(tmp_path, monkeypatch) -> None:
    meta = _tv_meta(tmp_path)
    calls: list[str] = []

    class Response:
        def __init__(self, status: int, payload: object) -> None:
            self.status_code = status
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self) -> object:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "arm",
                    request=httpx.Request("GET", "https://arm.haglund.dev"),
                    response=httpx.Response(self.status_code),
                )

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, params: dict | None = None) -> Response:
            calls.append(url)
            if len(calls) == 1:
                return Response(500, [_mapping()])
            return Response(200, [_mapping()])

    monkeypatch.setattr(
        "src.integrations.external_apis.tmdb.httpx.AsyncClient", Client
    )

    assert asyncio.run(_get_arm_mal_id(meta, 2)) == 0
    assert asyncio.run(_get_arm_mal_id(meta, 2)) == 123
    assert asyncio.run(_get_arm_mal_id(meta, 2)) == 123
    assert len(calls) == 2


def test_get_romaji_uses_the_arm_mal_id(tmp_path, monkeypatch) -> None:
    meta = Meta(
        base_dir=str(tmp_path),
        category="TV",
        tmdb_id=345,
        filename="Astral.Journey.S02E01.mkv",
    )
    posts: list[dict] = []

    class Response:
        def __init__(self, payload: dict, text: str = "") -> None:
            self.status_code = 200
            self._payload = payload
            self.text = text

        def json(self) -> dict:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, params: dict | None = None) -> Response:
            return Response([_mapping()])

        async def post(self, url: str, json: dict | None = None) -> Response:
            posts.append(json or {})
            return Response(
                {
                    "data": {
                        "Page": {
                            "media": [
                                {
                                    "idMal": 123,
                                    "title": {
                                        "romaji": "Astral Journey",
                                        "english": "Astral Journey",
                                    },
                                    "episodes": 12,
                                    "seasonYear": 2025,
                                }
                            ]
                        }
                    }
                }
            )

    monkeypatch.setattr(
        "src.integrations.external_apis.tmdb.httpx.AsyncClient", Client
    )
    result = asyncio.run(get_romaji("Astral Journey", 0, meta))

    assert result[:5] == ("Astral Journey", 123, "Astral Journey", "2025", 12)
    assert posts[0]["variables"] == {"search": 123}


def _dts_tracks(lossy_overrides: dict | None = None) -> tuple[dict, dict]:
    shared = {
        "@type": "Audio",
        "Duration": "600000",
        "FrameRate": "93.750",
        "FrameCount": "56250",
        "Language": "en",
    }
    hd = {
        **shared,
        "Format": "DTS",
        "Format_Commercial_IfAny": "DTS-HD Master Audio",
    }
    lossy = {**shared, "Format": "DTS", **(lossy_overrides or {})}
    return hd, lossy


def test_commentary_dts_is_not_a_core_duplicate() -> None:
    hd, lossy = _dts_tracks({"Title": "Director Commentary"})
    dts_core_additional_check(
        Meta(mediainfo={"media": {"track": [hd, lossy]}}, unattended=True)
    )
    hd, service = _dts_tracks({"ServiceKind": "CM / C"})
    dts_core_additional_check(
        Meta(mediainfo={"media": {"track": [service, hd]}}, unattended=True)
    )


def test_main_dts_core_still_blocks_the_upload() -> None:
    hd, lossy = _dts_tracks()
    commentary = {**lossy, "Title": "Director Commentary"}
    with pytest.raises(LossyDtsDuplicateError):
        dts_core_additional_check(
            Meta(
                mediainfo={"media": {"track": [hd, commentary, lossy]}},
                unattended=True,
            )
        )


def test_ffmpeg_does_not_inherit_cli_stdin(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*args: object, **kwargs: object) -> Process:
        captured.update(kwargs)
        return Process()

    class Command:
        def compile(self) -> list[str]:
            return ["ffmpeg", "-i", "in.mkv", "out.png"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    code, _stdout, _stderr = asyncio.run(run_ffmpeg(Command()))

    assert code == 0
    assert captured["stdin"] is asyncio.subprocess.DEVNULL


def test_guessit_list_keeps_the_last_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "src.services.media_source_service.guessit_fn",
        lambda _video, _options=None: {"source": ["VHS", "BluRay"]},
    )

    source, release_type = asyncio.run(
        get_source(
            "ENCODE",
            "VHS.85.2023.1080p.BluRay.x264-GROUP.mkv",
            "VHS.85.2023.1080p.BluRay.x264-GROUP.mkv",
            "",
            Meta(category="MOVIE"),
            "case",
            str(tmp_path),
        )
    )

    assert source == "BluRay"
    assert release_type == "ENCODE"


def test_bluray_lookup_runs_for_empty_region_or_distributor() -> None:
    empty = Meta(is_disc="BDMV", imdb_id=1234567, distributor="", region="")
    missing_region = Meta(
        is_disc="BDMV", imdb_id=1234567, distributor="Criterion", region=""
    )
    complete = Meta(
        is_disc="BDMV", imdb_id=1234567, distributor="Criterion", region="B"
    )

    assert _bluray_lookup_allowed(empty, True) is True
    assert _bluray_lookup_allowed(missing_region, True) is True
    assert _bluray_lookup_allowed(complete, True) is False
