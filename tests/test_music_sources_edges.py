from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx
import pytest

from src.domain_models.music import AudioTrack, MetadataSource, MusicRelease
from src.integrations.external_apis import music_sources
from src.integrations.external_apis.music_sources import DiscogsEnricher, MusicBrainzEnricher


async def async_value(value: Any = None) -> Any:
    return value


class Response:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status_code = status
        self.request = httpx.Request("GET", "https://music.invalid")

    def json(self) -> Any:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("failed", request=self.request, response=response)


class Client:
    queue: ClassVar[list[object]] = []
    calls: ClassVar[list[tuple[str, dict[str, object]]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> Response:
        type(self).calls.append((url, dict(kwargs)))
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.calls = []


@pytest.fixture(autouse=True)
def reset(monkeypatch: pytest.MonkeyPatch) -> None:
    Client.reset()
    monkeypatch.setattr(music_sources.httpx, "AsyncClient", Client)
    MusicBrainzEnricher._cache.clear()
    DiscogsEnricher._cache.clear()
    MusicBrainzEnricher._last_request = 0
    DiscogsEnricher._last_request = 0

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(music_sources.asyncio, "sleep", no_sleep)


def release(tmp_path: Path, tracks: int = 1, duration: float = 60) -> MusicRelease:
    value = MusicRelease(root=str(tmp_path))
    value.tracks = [AudioTrack(path=f"track-{index}.flac", relative_path=f"track-{index}.flac", format="FLAC", codec="FLAC", duration=duration) for index in range(tracks)]
    value.set_field("artist", "Artist", MetadataSource.FILE_TAG, 1.0)
    value.set_field("album", "Album", MetadataSource.FILE_TAG, 1.0)
    value.set_field("media", "WEB", MetadataSource.INFERRED, 0.5)
    return value


def test_cache_helpers_none_invalid_and_write_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert music_sources._music_cache_path("", "p", "k", "x") is None
    path = music_sources._music_cache_path(str(tmp_path), "p", "k", "secret query")
    assert path is not None and "secret query" not in str(path)
    assert asyncio.run(music_sources._read_music_cache(None)) is None
    assert asyncio.run(music_sources._read_music_cache(path)) is None
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    assert asyncio.run(music_sources._read_music_cache(path)) is None
    asyncio.run(music_sources._write_music_cache(None, {}))
    asyncio.run(music_sources._write_music_cache(path, {"ok": True}))
    assert path.read_text(encoding="utf-8").strip().startswith("{")
    monkeypatch.setattr(Path, "write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read only")))
    asyncio.run(music_sources._write_music_cache(path, {"ok": True}))
    asyncio.run(music_sources._write_music_cache(path, {1, 2}))


def test_musicbrainz_find_all_cache_and_http_paths(tmp_path: Path) -> None:
    enricher = MusicBrainzEnricher(base_dir=str(tmp_path))
    key = ("artist", "album", "1", "web", "")
    MusicBrainzEnricher._cache[key] = {"id": "memory"}
    assert asyncio.run(enricher._find_release("Artist", "Album", 1, "WEB", ""))["id"] == "memory"
    MusicBrainzEnricher._cache.clear()

    cache = music_sources._music_cache_path(str(tmp_path), "musicbrainz", "release_search", "\x1f".join(key))
    assert cache is not None
    asyncio.run(music_sources._write_music_cache(cache, {"not_found": True}))
    assert asyncio.run(enricher._find_release("Artist", "Album", 1, "WEB", "")) is None
    cache.unlink()
    MusicBrainzEnricher._cache.clear()

    exact = {"id": "remote", "title": "Album", "score": 100, "barcode": "12345678", "media": [{"format": "Digital Media", "track-count": 1}]}
    Client.reset(Response({"releases": [exact]}))
    result = asyncio.run(enricher._find_release("Artist", "Album", 1, "WEB", "12345678"))
    assert result and result["id"] == "remote"
    assert "barcode:12345678" in str(Client.calls[0][1]["params"])

    MusicBrainzEnricher._cache.clear()
    Client.reset(Response({"releases": []}))
    assert asyncio.run(enricher._find_release("Artist", "Missing", 0)) is None
    MusicBrainzEnricher._cache.clear()
    Client.reset(httpx.RequestError("offline", request=httpx.Request("GET", "https://musicbrainz.org")))
    assert asyncio.run(enricher._find_release("Artist", "Error", 0)) is None


def test_musicbrainz_enrich_and_helper_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enricher = MusicBrainzEnricher(base_dir=str(tmp_path))
    asyncio.run(enricher.enrich(MusicRelease(root=str(tmp_path))))
    target = release(tmp_path)
    monkeypatch.setattr(enricher, "_find_release", lambda *_args, **_kwargs: async_value(None))
    asyncio.run(enricher.enrich(target))
    result = {
        "id": "mb1",
        "title": "Album",
        "release-group": "bad",
        "date": "2024-01-01",
        "media": [{"format": "Digital Media", "track-count": 1}],
        "artist-credit": [{"artist": {"name": "Artist"}}],
    }
    monkeypatch.setattr(enricher, "_find_release", lambda *_args, **_kwargs: async_value(result))
    asyncio.run(enricher.enrich(target))
    assert target.external_ids["musicbrainz_release"] == "mb1"
    assert "musicbrainz_release_group" not in target.external_ids
    assert MusicBrainzEnricher._track_count({"track-count": "bad"}) == 0
    assert MusicBrainzEnricher._artists({"artist-credit": ["bad", {"artist": "bad", "name": "Fallback"}, {"artist": {"name": "One"}}, {"artist": {"name": "One"}}]}) == [
        "Fallback",
        "One",
    ]


def test_discogs_parse_and_enrich_release_master_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert DiscogsEnricher.parse_reference(None) is None
    assert DiscogsEnricher.parse_reference("release 123") == ("release", "123")
    assert DiscogsEnricher.parse_reference("123", "master") == ("master", "123")
    assert DiscogsEnricher.parse_reference("invalid") is None
    enricher = DiscogsEnricher(token="".join(("tok", "en")), base_dir=str(tmp_path))
    results = {
        ("releases", "1"): {"id": 1, "master_id": 2, "title": "Artist - Album", "artists": [{"name": "Artist"}]},
        ("masters", "2"): {"id": 2, "main_release": 3, "title": "Artist - Album", "artists": [{"name": "Artist"}], "year": 2020},
        ("releases", "3"): {"id": 3, "title": "Artist - Album", "artists": [{"name": "Artist"}]},
    }

    async def get(resource: str, identifier: str) -> dict[str, Any] | None:
        return results.get((resource, identifier))

    monkeypatch.setattr(enricher, "_get", get)
    target = release(tmp_path)
    asyncio.run(enricher.enrich(target, release_id="1"))
    assert target.external_ids["discogs_release"] == "1" and target.external_ids["discogs_master"] == "2"
    target = release(tmp_path)
    asyncio.run(enricher.enrich(target, master_id="2"))
    assert target.external_ids["discogs_master"] == "2" and target.external_ids["discogs_release"] == "3"
    monkeypatch.setattr(enricher, "_get", lambda *_args: async_value(None))
    asyncio.run(enricher.enrich(release(tmp_path), release_id="404", master_id="405"))


def test_discogs_find_cache_remote_and_errors(tmp_path: Path) -> None:
    no_token = DiscogsEnricher(base_dir=str(tmp_path))
    assert asyncio.run(no_token.find_exact_releases("", "Album")) == []
    assert asyncio.run(no_token.find_exact_releases("Artist", "")) == []
    assert asyncio.run(no_token.find_exact_releases("Artist", "Album")) == []

    enricher = DiscogsEnricher(token="".join(("tok", "en")), base_dir=str(tmp_path))
    key = ("search", "artist\x1falbum")
    DiscogsEnricher._cache[key] = {"results": [{"id": 1}]}
    assert asyncio.run(enricher.find_exact_releases("Artist", "Album")) == [{"id": 1}]
    DiscogsEnricher._cache.clear()

    cache = music_sources._music_cache_path(str(tmp_path), "discogs", "release_search", key[1])
    assert cache is not None
    asyncio.run(music_sources._write_music_cache(cache, [{"id": 2}]))
    assert asyncio.run(enricher.find_exact_releases("Artist", "Album")) == [{"id": 2}]
    cache.unlink()
    DiscogsEnricher._cache.clear()

    payload = {"results": [{"id": 3, "title": "Artist - Album"}, {"id": 4, "title": "Different - Album"}, "bad"]}
    Client.reset(Response(payload))
    assert asyncio.run(enricher.find_exact_releases("Artist", "Album")) == [{"id": 3, "title": "Artist - Album"}]

    DiscogsEnricher._cache.clear()
    Client.reset(Response([]))
    assert asyncio.run(enricher.find_exact_releases("Artist", "Other")) == []
    DiscogsEnricher._cache.clear()
    Client.reset(Response(ValueError("bad json")))
    assert asyncio.run(enricher.find_exact_releases("Artist", "Broken")) == []


def test_discogs_get_cache_remote_invalid_and_error(tmp_path: Path) -> None:
    enricher = DiscogsEnricher(token="".join(("tok", "en")), base_dir=str(tmp_path))
    DiscogsEnricher._cache[("releases", "1")] = {"id": 1}
    assert asyncio.run(enricher._get("releases", "1")) == {"id": 1}
    DiscogsEnricher._cache.clear()

    path = music_sources._music_cache_path(str(tmp_path), "discogs", "releases", "2")
    assert path is not None
    asyncio.run(music_sources._write_music_cache(path, {"id": 2}))
    assert asyncio.run(enricher._get("releases", "2")) == {"id": 2}
    path.unlink()
    DiscogsEnricher._cache.clear()

    Client.reset(Response({"id": 3}))
    assert asyncio.run(enricher._get("releases", "3")) == {"id": 3}
    DiscogsEnricher._cache.clear()
    Client.reset(Response([]))
    assert asyncio.run(enricher._get("releases", "4")) is None
    DiscogsEnricher._cache.clear()
    Client.reset(httpx.RequestError("offline", request=httpx.Request("GET", "https://discogs.com")))
    assert asyncio.run(enricher._get("releases", "5")) is None


def test_discogs_master_artist_edges(tmp_path: Path) -> None:
    target = release(tmp_path)
    DiscogsEnricher._apply_master(
        target,
        {
            "id": 9,
            "uri": "https://discogs.com/master/9",
            "year": "Released 1999",
            "artists": [{"name": "Artist (2)"}, {"name": "Artist (2)"}, "bad"],
        },
    )
    assert target.external_ids["discogs_master"] == "9"
    assert target.external_ids["discogs_master_url"].endswith("/9")
    assert target.get("year") == "1999"
    assert DiscogsEnricher._artists({"artists": "bad"}) == []


class Lock:
    def __init__(self, enter=None) -> None:
        self.enter = enter

    async def __aenter__(self):
        if self.enter:
            self.enter()
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_remaining_musicbrainz_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enricher = MusicBrainzEnricher(base_dir=str(tmp_path))
    target = release(tmp_path)
    result = {
        "id": "mb2",
        "title": "Album",
        "release-group": {"id": "group", "first-release-date": "2020-01-01", "primary-type": "Album"},
        "date": "2024-01-01",
        "media": [{"format": "Digital Media", "track-count": 1}],
        "artist-credit": [{"artist": {"name": "Artist"}}],
    }
    monkeypatch.setattr(enricher, "_find_release", lambda *_args, **_kwargs: async_value(result))
    asyncio.run(enricher.enrich(target))
    assert target.external_ids["musicbrainz_release_group"] == "group"

    assert MusicBrainzEnricher._select_release([{"title": "Different"}], "Album", 0) is None
    assert MusicBrainzEnricher._matches_catalogue_or_barcode(
        {"label-info": ["bad", {"catalog-number": "CAT-1"}]},
        "CAT-1",
    )

    enricher = MusicBrainzEnricher(base_dir=str(tmp_path))
    key = ("artist", "album", "1", "web", "")
    MusicBrainzEnricher._cache.clear()
    monkeypatch.setattr(MusicBrainzEnricher, "_lock", Lock(lambda: MusicBrainzEnricher._cache.__setitem__(key, {"id": "inside"})))
    assert asyncio.run(enricher._find_release("Artist", "Album", 1, "WEB", "")) == {"id": "inside"}

    MusicBrainzEnricher._cache.clear()
    reads = iter((None, {"not_found": True}))
    monkeypatch.setattr(music_sources, "_read_music_cache", lambda _path: async_value(next(reads)))
    monkeypatch.setattr(MusicBrainzEnricher, "_lock", Lock())
    assert asyncio.run(enricher._find_release("Artist", "Album", 1, "WEB", "")) is None

    MusicBrainzEnricher._cache.clear()
    reads = iter((None, {"id": "disk-inside"}))
    monkeypatch.setattr(music_sources, "_read_music_cache", lambda _path: async_value(next(reads)))
    assert asyncio.run(enricher._find_release("Artist", "Album", 1, "WEB", "")) == {"id": "disk-inside"}


def test_remaining_discogs_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enricher = DiscogsEnricher(token="".join(("tok", "en")), base_dir=str(tmp_path))
    key = ("search", "artist\x1falbum")
    DiscogsEnricher._cache.clear()
    monkeypatch.setattr(DiscogsEnricher, "_lock", Lock(lambda: DiscogsEnricher._cache.__setitem__(key, {"results": [{"id": 10}]})))
    assert asyncio.run(enricher.find_exact_releases("Artist", "Album")) == [{"id": 10}]

    DiscogsEnricher._cache.clear()
    reads = iter((None, [{"id": 11}]))
    monkeypatch.setattr(music_sources, "_read_music_cache", lambda _path: async_value(next(reads)))
    monkeypatch.setattr(DiscogsEnricher, "_lock", Lock())
    assert asyncio.run(enricher.find_exact_releases("Artist", "Album")) == [{"id": 11}]

    get_key = ("releases", "20")
    DiscogsEnricher._cache.clear()
    monkeypatch.setattr(DiscogsEnricher, "_lock", Lock(lambda: DiscogsEnricher._cache.__setitem__(get_key, {"id": 20})))
    monkeypatch.setattr(music_sources, "_read_music_cache", lambda _path: async_value(None))
    assert asyncio.run(enricher._get("releases", "20")) == {"id": 20}

    DiscogsEnricher._cache.clear()
    reads = iter((None, {"id": 21}))
    monkeypatch.setattr(DiscogsEnricher, "_lock", Lock())
    monkeypatch.setattr(music_sources, "_read_music_cache", lambda _path: async_value(next(reads)))
    assert asyncio.run(enricher._get("releases", "21")) == {"id": 21}

    assert not DiscogsEnricher._is_exact_release({"title": "No Separator"}, "Artist", "Album")
    target = release(tmp_path)
    DiscogsEnricher._apply_release(
        target,
        {
            "id": 1,
            "uri": "https://discogs.com/release/1",
            "title": "Artist - Album",
            "artists": [{"name": "Artist"}],
            "notes": "Notes",
        },
    )
    assert target.external_ids["discogs_release_url"].endswith("/1")
    assert target.get("discogs_notes") == "Notes"
    assert DiscogsEnricher._first_label({"labels": "bad"}) is None
    assert DiscogsEnricher._genres({"genres": "bad", "styles": ["Rock"]}) == ["Rock"]
    assert DiscogsEnricher._release_type("bad") == ""


def test_final_music_source_guard_branches() -> None:
    assert MusicBrainzEnricher._select_release({"bad": True}, "Album", 0) is None
    assert DiscogsEnricher._release_type([{"descriptions": ["Unknown"]}]) == ""
