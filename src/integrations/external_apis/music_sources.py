"""Opt-in external release enrichment with bounded requests and in-memory cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx

from src.domain_models.music import MetadataSource, MusicRelease
from src.integrations.observability.runtime_support import logger


def _music_cache_path(base_dir: str, provider: str, kind: str, key: str) -> Path | None:
    """Return a stable cache location without exposing query text in filenames."""
    if not base_dir:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return Path(base_dir) / "tmp" / "music_metadata_cache" / provider / kind / f"{digest}.json"


async def _read_music_cache(path: Path | None) -> Any | None:
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    except OSError, ValueError, json.JSONDecodeError:
        return None


async def _write_music_cache(path: Path | None, value: Any) -> None:
    if path is None:
        return
    try:
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError, TypeError, ValueError:
        pass


def _unique_nonempty_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except TypeError, ValueError:
        return 0


class MusicBrainzEnricher:
    """Small read-only MusicBrainz client.

    MusicBrainz is used as a corroborating source, never as an implicit override
    of complete local tags.  Its documented one-request-per-second etiquette is
    observed across all instances in this process.
    """

    _cache: ClassVar[dict[tuple[str, str, str, str, str], dict[str, Any] | None]] = {}
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _last_request: ClassVar[float] = 0.0

    def __init__(self, user_agent: str = "Upload-Assistant/2.x (+https://github.com/wastaken7/Upload-Assistant)", base_dir: str = "") -> None:
        self.user_agent = user_agent
        self.base_dir = base_dir

    async def enrich(self, release: MusicRelease) -> None:
        identity = self._release_identity(release)
        if identity is None:
            return
        artist, album, media, catalogue_number = identity
        result = await self._find_release(artist, album, len(release.tracks), media, catalogue_number)
        if result is not None:
            self._apply_result(release, result)

    @staticmethod
    def _release_identity(release: MusicRelease) -> tuple[str, str, str, str] | None:
        artist = str(release.get("artist", ""))
        album = str(release.get("album", ""))
        if not artist or not album:
            return None
        media = str(release.get("media", "")).strip()
        catalogue = release.get("directory_catalogue_number", "") or release.get("release_catalogue_number", "") or release.get("catalogue_number", "")
        return artist, album, media, str(catalogue).strip()

    @classmethod
    def _apply_result(cls, release: MusicRelease, result: dict[str, Any]) -> None:
        release.external_ids["musicbrainz_release"] = str(result.get("id", ""))
        release_group = cls._release_group(result)
        group_id = release_group.get("id")
        if group_id:
            release.external_ids["musicbrainz_release_group"] = str(group_id)
        release.set_field("musicbrainz_release", result.get("id"), MetadataSource.EXTERNAL, 0.9)
        _set_external_release_type(release, cls._release_type(result), 0.72, "MusicBrainz")
        release.set_field("year", str(release_group.get("first-release-date", ""))[:4], MetadataSource.EXTERNAL, 0.7)
        release.set_field("release_year", str(result.get("date", ""))[:4], MetadataSource.EXTERNAL, 0.78)
        release.set_field("release_label", cls._label(result), MetadataSource.EXTERNAL, 0.78)
        release.set_field("release_catalogue_number", cls._catalogue_number(result), MetadataSource.EXTERNAL, 0.78)
        artists = cls._artists(result)
        release.set_field("artists", artists, MetadataSource.EXTERNAL, 0.8)
        release.set_field("artist", " & ".join(artists), MetadataSource.EXTERNAL, 0.8)

    @staticmethod
    def _release_group(result: dict[str, Any]) -> dict[str, Any]:
        value = result.get("release-group", {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    async def _find_release(self, artist: str, album: str, track_count: int = 0, media: str = "", catalogue_number: str = "") -> dict[str, Any] | None:
        key = (artist.casefold(), album.casefold(), str(track_count), media.casefold(), catalogue_number.casefold())
        cache_path = _music_cache_path(self.base_dir, "musicbrainz", "release_search", "\x1f".join(map(str, key)))
        hit, result = await self._cached_release(key, cache_path)
        if hit:
            return result
        async with self._lock:
            hit, result = await self._cached_release(key, cache_path)
            return result if hit else await self._request_release(key, cache_path, artist, album, track_count, media, catalogue_number)

    @classmethod
    async def _cached_release(cls, key: tuple[str, str, str, str, str], cache_path: Path | None) -> tuple[bool, dict[str, Any] | None]:
        if key in cls._cache:
            return True, cls._cache[key]
        cached = await _read_music_cache(cache_path)
        if not isinstance(cached, dict):
            return False, None
        result = None if cached.get("not_found") is True else cached
        cls._cache[key] = result
        return True, result

    async def _request_release(
        self,
        key: tuple[str, str, str, str, str],
        cache_path: Path | None,
        artist: str,
        album: str,
        track_count: int,
        media: str,
        catalogue_number: str,
    ) -> dict[str, Any] | None:
        await self._wait_for_request_slot()
        try:
            result = await self._search_remote(artist, album, track_count, media, catalogue_number)
        except httpx.HTTPError, ValueError:
            result, request_succeeded = None, False
        else:
            request_succeeded = True
        type(self)._last_request = time.monotonic()
        type(self)._cache[key] = result
        if request_succeeded:
            await _write_music_cache(cache_path, result if result is not None else {"not_found": True})
        return result

    @classmethod
    async def _wait_for_request_slot(cls) -> None:
        delay = 1.0 - (time.monotonic() - cls._last_request)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _search_remote(self, artist: str, album: str, track_count: int, media: str, catalogue_number: str) -> dict[str, Any] | None:
        barcode = self._barcode(catalogue_number)
        query = f"barcode:{barcode}" if barcode else f'artist:"{artist}" AND release:"{album}"'
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), headers={"User-Agent": self.user_agent}) as client:
            response = await client.get("https://musicbrainz.org/ws/2/release/", params={"query": query, "fmt": "json", "limit": 25})
            response.raise_for_status()
            payload = response.json()
        releases = payload.get("releases", []) if isinstance(payload, dict) else []
        return self._select_release(releases, album, track_count, media, catalogue_number)

    @classmethod
    def _select_release(cls, releases: Any, album: str, track_count: int, media: str = "", catalogue_number: str = "") -> dict[str, Any] | None:
        """Return only a MusicBrainz release that corroborates local evidence."""
        if not isinstance(releases, list):
            return None
        candidates = cls._title_candidates(releases, album)
        candidates = cls._track_candidates(candidates, track_count)
        candidates = cls._media_candidates(candidates, media)
        candidates = cls._catalogue_candidates(candidates, catalogue_number)
        return max(candidates, key=cls._candidate_score) if candidates else None

    @classmethod
    def _title_candidates(cls, releases: list[Any], album: str) -> list[dict[str, Any]]:
        expected = cls._normalise_title(album)
        mappings = (item for item in releases if isinstance(item, dict))
        return [item for item in mappings if cls._normalise_title(item.get("title", "")) == expected]

    @classmethod
    def _track_candidates(cls, releases: list[dict[str, Any]], track_count: int) -> list[dict[str, Any]]:
        return releases if not track_count else [item for item in releases if cls._track_count(item) == track_count]

    @classmethod
    def _media_candidates(cls, releases: list[dict[str, Any]], media: str) -> list[dict[str, Any]]:
        return releases if not media else [item for item in releases if cls._has_compatible_media(item, media)]

    @classmethod
    def _catalogue_candidates(cls, releases: list[dict[str, Any]], catalogue_number: str) -> list[dict[str, Any]]:
        return releases if not catalogue_number else [item for item in releases if cls._matches_catalogue_or_barcode(item, catalogue_number)]

    @staticmethod
    def _candidate_score(item: dict[str, Any]) -> int:
        return _safe_int(item.get("score", 0))

    @staticmethod
    def _barcode(value: Any) -> str:
        """Return an EAN/UPC-like value, or an empty string for a catalogue ID."""
        digits = re.sub(r"\D", "", str(value or ""))
        return digits if 8 <= len(digits) <= 14 else ""

    @staticmethod
    def _matches_catalogue_or_barcode(result: dict[str, Any], value: str) -> bool:
        expected = str(value).strip().casefold()
        barcode = MusicBrainzEnricher._barcode(value)
        if barcode and MusicBrainzEnricher._barcode(result.get("barcode", "")) == barcode:
            return True
        return any(isinstance(info, dict) and str(info.get("catalog-number", "")).strip().casefold() == expected for info in result.get("label-info", []))

    @classmethod
    def _has_compatible_media(cls, result: dict[str, Any], media: str) -> bool:
        expected = {"web": "digital media"}.get(str(media).strip().casefold(), str(media).strip().casefold())
        return any(cls._media_format_matches(expected, candidate) for candidate in cls._media_formats(result))

    @staticmethod
    def _media_formats(result: dict[str, Any]) -> list[str]:
        values = result.get("media", [])
        if not isinstance(values, list):
            return []
        return [str(item.get("format", "")).strip().casefold() for item in values if isinstance(item, dict)]

    @staticmethod
    def _media_format_matches(expected: str, candidate: str) -> bool:
        if candidate == expected:
            return True
        return expected == "vinyl" and "vinyl" in candidate

    @staticmethod
    def _normalise_title(value: Any) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").casefold())

    @classmethod
    def _track_count(cls, result: dict[str, Any]) -> int:
        media_total = sum(cls._media_track_counts(result))
        return media_total or _safe_int(result.get("track-count", 0))

    @staticmethod
    def _media_track_counts(result: dict[str, Any]) -> list[int]:
        media = result.get("media", [])
        if not isinstance(media, list):
            return []
        return [_safe_int(item.get("track-count", 0)) for item in media if isinstance(item, dict)]

    @staticmethod
    def _release_type(result: dict[str, Any]) -> str:
        release_group = result.get("release-group", {})
        if not isinstance(release_group, dict):
            return ""
        types = release_group.get("primary-type", "")
        return str(types).title() if types else ""

    @staticmethod
    def _label(result: dict[str, Any]) -> str:
        info = result.get("label-info", [])
        return str(info[0].get("label", {}).get("name", "")) if info else ""

    @staticmethod
    def _catalogue_number(result: dict[str, Any]) -> str:
        info = result.get("label-info", [])
        return str(info[0].get("catalog-number", "")) if info else ""

    @classmethod
    def _artists(cls, result: dict[str, Any]) -> list[str]:
        credits = result.get("artist-credit", [])
        if not isinstance(credits, list):
            return []
        return _unique_nonempty_strings([cls._artist_credit_name(credit) for credit in credits])

    @staticmethod
    def _artist_credit_name(credit: Any) -> str:
        if not isinstance(credit, dict):
            return ""
        artist = credit.get("artist", {})
        value = artist.get("name", "") if isinstance(artist, dict) else credit.get("name", "")
        return str(value)


class DiscogsEnricher:
    """Resolve explicit or unambiguous Discogs release references.

    An explicit release ID identifies the exact pressing.  When no ID is
    supplied, a title/artist search is accepted only after an exact match has
    been established (and selected by the user where it is ambiguous).  The
    client is read-only, caches responses per process and serialises requests
    to one per second, comfortably below Discogs' public API limit.
    """

    _cache: ClassVar[dict[tuple[str, str], dict[str, Any] | None]] = {}
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _last_request: ClassVar[float] = 0.0

    def __init__(self, token: str = "", user_agent: str = "Upload-Assistant/2.x (+https://github.com/wastaken7/Upload-Assistant)", base_dir: str = "") -> None:
        self.token = token.strip()
        self.user_agent = user_agent
        self.base_dir = base_dir

    @classmethod
    def parse_reference(cls, value: Any, default_kind: str = "release") -> tuple[str, str] | None:
        """Accept a Discogs URL, ``release/123``/``master:123`` or a bare ID."""
        text = str(value or "").strip()
        if not text:
            return None
        match = re.search(r"(?:discogs\.com/|^)(release|master)[/:](\d+)(?:[-/?#]|$)", text, re.I)
        if match:
            return match.group(1).casefold(), match.group(2)
        match = re.fullmatch(r"(?:release|master)\s+(\d+)", text, re.I)
        if match:
            kind = text.split(maxsplit=1)[0].casefold()
            return kind, match.group(1)
        if text.isdigit():
            return default_kind, text
        return None

    async def enrich(self, release: MusicRelease, *, release_id: str = "", master_id: str = "") -> None:
        """Add corroborating data for the supplied exact Discogs identifiers."""
        release_data: dict[str, Any] | None = None
        if release_id:
            release_data = await self._get("releases", release_id)
            if release_data:
                self._apply_release(release, release_data)
                master_id = master_id or str(release_data.get("master_id", ""))
            else:
                logger.warning(f"[yellow]MUSIC: Discogs release {release_id} was not found or could not be read.[/yellow]")
        if master_id:
            master_data = await self._get("masters", master_id)
            if master_data:
                self._apply_master(release, master_data)
                logger.info(f"[cyan]MUSIC: corroborated original release data from Discogs master {master_id}.[/cyan]")
                # A master alone is not a concrete pressing.  Its main release
                # supplies label/catalogue data, so fetch that single linked
                # record when the caller did not already specify a release.
                if release_data is None and str(master_data.get("main_release", "")).isdigit():
                    release_data = await self._get("releases", str(master_data["main_release"]))
                    if release_data:
                        self._apply_release(release, release_data)
                        logger.info(f"[cyan]MUSIC: enriched concrete release from Discogs master {master_id}.[/cyan]")
            else:
                logger.warning(f"[yellow]MUSIC: Discogs master {master_id} was not found or could not be read.[/yellow]")

    async def find_exact_releases(self, artist: str, album: str) -> list[dict[str, Any]]:
        """Return Discogs releases whose displayed artist and title are exact.

        Search is deliberately stricter than the API query: partial titles,
        compilations with a matching word, and a different artist are ignored.
        Multiple pressings are retained for the caller to choose from.
        """
        if not artist.strip() or not album.strip():
            return []
        if not self.token:
            logger.warning("[yellow]MUSIC: Discogs exact-match search skipped because no Discogs token is configured.[/yellow]")
            return []
        key = ("search", f"{artist.casefold()}\x1f{album.casefold()}")
        if key in self._cache:
            result = self._cache[key]
            return list(result.get("results", [])) if result else []
        cache_path = _music_cache_path(self.base_dir, "discogs", "release_search", key[1])
        cached = await _read_music_cache(cache_path)
        if isinstance(cached, list):
            self._cache[key] = {"results": cached}
            return list(cached)
        async with self._lock:
            if key in self._cache:
                result = self._cache[key]
                return list(result.get("results", [])) if result else []
            cached = await _read_music_cache(cache_path)
            if isinstance(cached, list):
                self._cache[key] = {"results": cached}
                return list(cached)
            delay = 1.0 - (time.monotonic() - type(self)._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            headers = {"User-Agent": self.user_agent}
            if self.token:
                headers["Authorization"] = f"Discogs token={self.token}"
            request_succeeded = False
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), headers=headers) as client:
                    response = await client.get(
                        "https://api.discogs.com/database/search",
                        params={"artist": artist, "release_title": album, "type": "release", "per_page": 100},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    results = payload.get("results", []) if isinstance(payload, dict) else []
                    matches = [item for item in results if isinstance(item, dict) and self._is_exact_release(item, artist, album)]
                    request_succeeded = True
            except httpx.HTTPError, ValueError:
                matches = []
            type(self)._last_request = time.monotonic()
            cached = {"results": matches}
            type(self)._cache[key] = cached
            if request_succeeded:
                await _write_music_cache(cache_path, matches)
            return list(matches)

    @staticmethod
    def _normalise_match(value: Any) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").casefold())

    @classmethod
    def _is_exact_release(cls, result: dict[str, Any], artist: str, album: str) -> bool:
        title = str(result.get("title", "")).strip()
        if " - " not in title:
            return False
        candidate_artist, candidate_album = title.split(" - ", 1)
        return cls._normalise_match(candidate_artist) == cls._normalise_match(artist) and cls._normalise_match(candidate_album) == cls._normalise_match(album)

    @classmethod
    def filter_releases_by_media(cls, releases: list[dict[str, Any]], media: Any) -> list[dict[str, Any]]:
        """Remove candidates whose Discogs physical/digital format conflicts.

        A release with no recognised Discogs medium is retained: search results
        are occasionally incomplete, and unknown is safer than a false reject.
        """
        target = {
            "WEB": {"file"},
            "CD": {"cd", "cdr"},
            "DVD": {"dvd", "dvd-r"},
            "BD": {"blu-ray", "blu-ray-r"},
            "Vinyl": {"vinyl", "shellac", "flexi-disc", "lacquer"},
            "Cassette": {"cassette"},
            "SACD": {"sacd"},
            "DAT": {"dat"},
        }.get(str(media or ""))
        if not target:
            return releases
        known = {
            "file",
            "cd",
            "cdr",
            "dvd",
            "dvd-r",
            "blu-ray",
            "blu-ray-r",
            "vinyl",
            "shellac",
            "flexi-disc",
            "lacquer",
            "cassette",
            "sacd",
            "dat",
            "reel-to-reel",
            "minidisc",
            "memory stick",
            "usb flash drive",
            "vhs",
            "laserdisc",
            "8-track cartridge",
            "dcc",
            "betamax",
            "hd-dvd",
        }
        filtered: list[dict[str, Any]] = []
        for release in releases:
            formats = release.get("format", [])
            formats = formats if isinstance(formats, list) else [formats]
            recognised = {str(value).casefold() for value in formats} & known
            if not recognised or recognised & target:
                filtered.append(release)
        return filtered

    @classmethod
    def filter_releases_by_catalogue(cls, releases: list[dict[str, Any]], catalogue: Any) -> list[dict[str, Any]]:
        """Keep exact catalogue matches, allowing only whitespace variation.

        Hyphens are significant in catalogue numbers: ``B001219802`` and
        ``B0012198-02`` can identify different releases.
        """
        wanted = re.sub(r"\s+", "", str(catalogue or "")).casefold()
        if not wanted:
            return releases
        matches = [release for release in releases if re.sub(r"\s+", "", str(release.get("catno", ""))).casefold() == wanted]
        return matches or releases

    async def _get(self, resource: str, identifier: str) -> dict[str, Any] | None:
        key = (resource, identifier)
        if key in self._cache:
            return self._cache[key]
        cache_path = _music_cache_path(self.base_dir, "discogs", resource, identifier)
        cached = await _read_music_cache(cache_path)
        if isinstance(cached, dict):
            self._cache[key] = cached
            return cached
        async with self._lock:
            if key in self._cache:
                return self._cache[key]
            cached = await _read_music_cache(cache_path)
            if isinstance(cached, dict):
                self._cache[key] = cached
                return cached
            delay = 1.0 - (time.monotonic() - type(self)._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            headers = {"User-Agent": self.user_agent}
            if self.token:
                headers["Authorization"] = f"Discogs token={self.token}"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), headers=headers) as client:
                    response = await client.get(f"https://api.discogs.com/{resource}/{identifier}")
                    response.raise_for_status()
                    result = response.json()
                    if not isinstance(result, dict):
                        result = None
            except httpx.HTTPError, ValueError:
                result = None
            type(self)._last_request = time.monotonic()
            type(self)._cache[key] = result
            if result is not None:
                await _write_music_cache(cache_path, result)
            return result

    @classmethod
    def _apply_release(cls, release: MusicRelease, result: dict[str, Any]) -> None:
        identifier = str(result.get("id", "")).strip()
        if identifier:
            release.external_ids["discogs_release"] = identifier
            release.set_field("discogs_release", identifier, MetadataSource.EXTERNAL, 0.9)
        uri = str(result.get("uri", "")).strip()
        if uri:
            release.external_ids["discogs_release_url"] = uri
        master_id = str(result.get("master_id", "")).strip()
        if master_id:
            release.external_ids["discogs_master"] = master_id
        cls._apply_common(release, result, release_record=True)
        label = cls._first_label(result)
        if label:
            cls._set_external(release, "release_label", label[0], 0.94)
            cls._set_external(release, "release_catalogue_number", label[1], 0.94)
        released = str(result.get("released", "")).strip()
        if released:
            cls._set_external(release, "retail_date", released, 0.90)
        year = cls._year(result.get("year"))
        if year:
            cls._set_external(release, "release_year", year, 0.92)
        country = str(result.get("country", "")).strip()
        if country:
            cls._set_external(release, "release_country", country, 0.86)
        media = cls._media(result.get("formats"))
        if media:
            cls._set_external(release, "media", media, 0.84)
        release_type = cls._release_type(result.get("formats"))
        if release_type:
            _set_external_release_type(release, release_type, 0.86, "Discogs")

    @classmethod
    def _apply_master(cls, release: MusicRelease, result: dict[str, Any]) -> None:
        identifier = str(result.get("id", "")).strip()
        if identifier:
            release.external_ids["discogs_master"] = identifier
            release.set_field("discogs_master", identifier, MetadataSource.EXTERNAL, 0.9)
        uri = str(result.get("uri", "")).strip()
        if uri:
            release.external_ids["discogs_master_url"] = uri
        cls._apply_common(release, result, release_record=False)
        year = cls._year(result.get("year"))
        if year:
            cls._set_external(release, "year", year, 0.90)

    @classmethod
    def _apply_common(cls, release: MusicRelease, result: dict[str, Any], *, release_record: bool) -> None:
        artists = cls._artists(result)
        if artists:
            cls._set_external(release, "artists", artists, 0.92)
            cls._set_external(release, "artist", " & ".join(artists), 0.92)
        title = cls._title(result, artists)
        if title:
            cls._set_external(release, "album", title, 0.92)
        genres = cls._genres(result)
        if genres:
            cls._set_external(release, "genres", genres, 0.86)
        if release_record:
            notes = str(result.get("notes", "")).strip()
            if notes:
                cls._set_external(release, "discogs_notes", notes, 0.5)

    @staticmethod
    def _set_external(release: MusicRelease, name: str, value: Any, confidence: float) -> None:
        """External data may fill/inform, but never replace local evidence."""
        existing = release.fields.get(name)
        protected_sources = {MetadataSource.USER, MetadataSource.FILE_TAG, MetadataSource.AUXILIARY}
        effective_confidence = 0.0 if existing and existing.source in protected_sources else confidence
        release.set_field(name, value, MetadataSource.EXTERNAL, effective_confidence)

    @staticmethod
    def _year(value: Any) -> str:
        match = re.search(r"\b(\d{4})\b", str(value or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _first_label(result: dict[str, Any]) -> tuple[str, str] | None:
        labels = result.get("labels", [])
        if not isinstance(labels, list):
            return None
        for label in labels:
            if isinstance(label, dict):
                name, catno = str(label.get("name", "")).strip(), str(label.get("catno", "")).strip()
                if name or catno:
                    return name, "" if catno.casefold() in {"none", "n/a", "na"} else catno
        return None

    @staticmethod
    def _artists(result: dict[str, Any]) -> list[str]:
        values = result.get("artists", [])
        if not isinstance(values, list):
            return []
        artists: list[str] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            name = re.sub(r"\s+\(\d+\)$", "", str(value.get("name", "")).strip())
            if name and name not in artists:
                artists.append(name)
        return artists

    @staticmethod
    def _title(result: dict[str, Any], artists: list[str]) -> str:
        title = str(result.get("title", "")).strip()
        for artist in artists:
            prefix = f"{artist} - "
            if title.casefold().startswith(prefix.casefold()):
                return title[len(prefix) :].strip()
        return title

    @staticmethod
    def _genres(result: dict[str, Any]) -> list[str]:
        values = result.get("genres", [])
        styles = result.get("styles", [])
        output: list[str] = []
        for group in (values, styles):
            if not isinstance(group, list):
                continue
            for value in group:
                text = str(value).strip()
                if text and text not in output:
                    output.append(text)
        return output

    @staticmethod
    def _media(formats: Any) -> str:
        if not isinstance(formats, list):
            return ""
        names = {str(item.get("name", "")).casefold() for item in formats if isinstance(item, dict)}
        mapping = {"cd": "CD", "file": "WEB", "vinyl": "Vinyl", "cassette": "Cassette", "sacd": "SACD", "dat": "DAT", "dvd": "DVD", "blu-ray": "BD"}
        mapped = {mapping[name] for name in names if name in mapping}
        return next(iter(mapped)) if len(mapped) == 1 else ""

    @staticmethod
    def _release_type(formats: Any) -> str:
        if not isinstance(formats, list):
            return ""
        descriptions = {str(description).casefold() for item in formats if isinstance(item, dict) for description in item.get("descriptions", [])}
        for name, needle in (("EP", "ep"), ("Single", "single"), ("Compilation", "compilation"), ("Live album", "live"), ("Album", "album")):
            if needle in descriptions:
                return name
        return ""


def _set_external_release_type(release: MusicRelease, value: str, confidence: float, provider: str) -> None:
    """Apply an external type only when it fits the local release structure."""
    release_type = str(value or "").strip()
    if not release_type:
        return
    track_count = len(release.tracks)
    duration = sum(track.duration or 0 for track in release.tracks)
    # Singles can legitimately contain a B-side or a small remix bundle, but
    # an album-length, many-track release is not corroborated by a conflicting
    # remote search hit.  Keep locally derived Album/EP data in that case.
    if release_type == "Single" and (track_count > 3 or duration > 20 * 60):
        message = f"Ignored external {provider} release type 'Single': local release has {track_count} track(s) and lasts {duration / 60:.0f} minutes."
        release.warnings.append(message)
        logger.warning(f"[yellow]MUSIC: {message}[/yellow]")
        return
    release.set_field("release_type", release_type, MetadataSource.EXTERNAL, confidence)
