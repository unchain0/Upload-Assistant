"""Bridge the music domain into Upload Assistant's shared ``Meta`` object."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import mutagen

from src.domain_models.errors import MediaInfoError, ReleasePathMissingError
from src.domain_models.music import MetadataSource, MusicRelease
from src.domain_models.release import Meta
from src.engines.music_validation import MusicValidator
from src.integrations.external_apis.music_sources import (
    DiscogsEnricher,
    MusicBrainzEnricher,
)
from src.integrations.filesystem.temp_paths import (
    artwork_dir,
    music_release_snapshot_path,
)
from src.integrations.media.music_analyzer import MusicReleaseAnalyzer
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import PathAwareEncoder


def _is_png_cover(data: bytes, mime: str) -> bool:
    return "png" in mime or data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_webp_cover(data: bytes, mime: str) -> bool:
    return "webp" in mime or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def _image_suffix(data: bytes, mime: str = "") -> str:
    """Infer a safe suffix for extracted embedded artwork."""
    normalized_mime = mime.casefold()
    if _is_png_cover(data, normalized_mime):
        return ".png"
    if _is_webp_cover(data, normalized_mime):
        return ".webp"
    return ".jpg"


def _load_tagged_audio(audio_path: str) -> Any | None:
    try:
        mutagen_module = cast(Any, mutagen)
        return mutagen_module.File(audio_path)
    except mutagen.MutagenError, OSError:  # pyright: ignore[reportPrivateImportUsage]
        return None


def _front_cover(items: list[Any]) -> Any | None:
    if not items:
        return None
    for item in items:
        if getattr(item, "type", None) == 3:
            return item
    return items[0]


def _picture_cover(audio: Any) -> tuple[bytes | None, str]:
    pictures = list(getattr(audio, "pictures", None) or [])
    picture = _front_cover(pictures)
    if picture is None:
        return None, ""
    return bytes(picture.data), str(getattr(picture, "mime", ""))


def _apic_cover(audio: Any) -> tuple[bytes | None, str]:
    tags = getattr(audio, "tags", None)
    if not tags:
        return None, ""
    apics = [
        value for key, value in tags.items() if str(key).startswith("APIC")
    ]
    picture = _front_cover(apics)
    if picture is None:
        return None, ""
    return bytes(picture.data), str(getattr(picture, "mime", ""))


def _mp4_cover(audio: Any) -> bytes | None:
    try:
        covers = audio["covr"]
    except KeyError, TypeError:
        return None
    return bytes(covers[0]) if covers else None


def _embedded_cover(audio: Any) -> tuple[bytes | None, str]:
    data, mime = _picture_cover(audio)
    if data is not None:
        return data, mime
    data, mime = _apic_cover(audio)
    if data is not None:
        return data, mime
    return _mp4_cover(audio), ""


def _write_music_cover(output_dir: Path, data: bytes, mime: str) -> Path:
    destination = output_dir / f"MUSIC_COVER{_image_suffix(data, mime)}"
    destination.write_bytes(data)
    return destination


def _extract_embedded_artwork(
    audio_paths: list[str], output_dir: Path
) -> Path | None:
    """Extract the first front-cover image from FLAC, ID3 or MP4 tags."""
    for audio_path in audio_paths:
        audio = _load_tagged_audio(audio_path)
        if audio is None:
            continue
        data, mime = _embedded_cover(audio)
        if data:
            return _write_music_cover(output_dir, data, mime)
    return None


async def prepare_music_cover(meta: Meta, release: Any) -> str:
    """Resolve local or embedded artwork to a hostable temporary/local path.

    This phase never uploads an image and never alters source files.  Hosting is
    deliberately deferred until the user has confirmed the upload workflow.
    """
    configured = Path(str(meta.artwork_path or ""))
    if configured.is_file():
        return str(configured)
    output_dir = artwork_dir(meta.base_dir, str(meta.uuid))
    extracted = await asyncio.to_thread(
        _extract_embedded_artwork,
        [track.path for track in release.tracks],
        output_dir,
    )
    if extracted:
        meta.artwork_path = str(extracted)
        return meta.artwork_path
    return ""


def _music_override_year(value: Any, name: str) -> str:
    """Return a safe four-digit CLI override year or ignore an invalid one."""
    # argparse/Meta use zero as the unset sentinel for optional year values.
    # It is not a user error and should not produce noise on every MUSIC run.
    if value in (None, "", 0, "0"):
        return ""
    try:
        year = int(value)
    except TypeError, ValueError:
        return ""
    if 1000 <= year <= 3000:
        return str(year)
    logger.warning(
        f"[yellow]MUSIC: ignoring invalid {name} override {value!r}; expected a four-digit year.[/yellow]"
    )
    return ""


_MUSIC_MEDIA_MAP = {
    "cd": "CD",
    "web": "WEB",
    "vinyl": "Vinyl",
    "dvd": "DVD",
    "bd": "BD",
    "soundboard": "Soundboard",
    "sacd": "SACD",
    "dat": "DAT",
    "cassette": "Cassette",
}
_MUSIC_RELEASE_TYPES = (
    "Album",
    "Soundtrack",
    "EP",
    "Anthology",
    "Compilation",
    "Sampler",
    "Single",
    "Demo",
    "Live album",
    "Split",
    "Remix",
    "Bootleg",
    "Interview",
    "Mixtape",
    "DJ Mix",
    "Concert recording",
    "Unknown",
)
_MUSIC_RELEASE_TYPE_MAP = {
    item.casefold(): item for item in _MUSIC_RELEASE_TYPES
}


def _set_user_music_field(release: Any, name: str, value: Any) -> None:
    release.set_field(name, value, MetadataSource.USER, 1.0, force=True)


def _optional_text(value: Any) -> str:
    return str(value).strip() if value else ""


def _music_artists(value: str) -> list[str]:
    return [
        part.strip() for part in re.split(r"\s+&\s+", value) if part.strip()
    ]


def _artist_list(artist: str) -> list[str]:
    artists = _music_artists(artist)
    return artists if artists else [artist]


def _apply_artist_album_overrides(meta: Meta, release: Any) -> None:
    artist = _optional_text(meta.music_artist)
    if artist:
        _set_user_music_field(release, "artist", artist)
        _set_user_music_field(release, "artists", _artist_list(artist))
    album = _optional_text(meta.music_album)
    if album:
        _set_user_music_field(release, "album", album)


def _apply_music_year_overrides(meta: Meta, release: Any) -> None:
    overrides = (
        ("year", meta.manual_year, "--year"),
        ("release_year", meta.music_release_year, "--music-release-year"),
        ("edition_year", meta.music_edition_year, "--music-edition-year"),
    )
    for field, raw_value, argument in overrides:
        value = _music_override_year(raw_value, argument)
        if value:
            _set_user_music_field(release, field, value)


def _music_media_override(meta: Meta) -> str | None:
    media = _MUSIC_MEDIA_MAP.get(str(meta.music_media or "").casefold())
    if media:
        return media
    return _MUSIC_MEDIA_MAP.get(str(meta.manual_source or "").casefold())


def _apply_music_identity_overrides(meta: Meta, release: Any) -> None:
    media = _music_media_override(meta)
    if media:
        _set_user_music_field(release, "media", media)
    release_type = _MUSIC_RELEASE_TYPE_MAP.get(
        str(meta.music_release_type or "").casefold()
    )
    if release_type:
        _set_user_music_field(release, "release_type", release_type)


def _music_genres(meta: Meta) -> list[str]:
    return list(
        filter(
            None,
            (item.strip() for item in str(meta.music_genres or "").split(",")),
        )
    )


def _apply_music_label_overrides(meta: Meta, release: Any) -> None:
    label = _optional_text(meta.music_label)
    if label:
        _set_user_music_field(release, "release_label", label)
    catalogue = _optional_text(meta.music_catalogue_number)
    if catalogue:
        _set_user_music_field(release, "release_catalogue_number", catalogue)
    genres = _music_genres(meta)
    if genres:
        _set_user_music_field(release, "genres", genres)


def _music_edition(meta: Meta) -> str:
    edition = meta.manual_edition
    if isinstance(edition, list):
        return " ".join(filter(None, (str(item).strip() for item in edition)))
    return str(edition or "").strip()


def _apply_music_edition_override(meta: Meta, release: Any) -> None:
    edition = _music_edition(meta)
    if edition:
        _set_user_music_field(release, "edition", edition)


def _apply_music_cli_overrides(meta: Meta, release: Any) -> None:
    """Apply intentional CLI values after analysis and before enrichment."""
    _apply_artist_album_overrides(meta, release)
    _apply_music_year_overrides(meta, release)
    _apply_music_identity_overrides(meta, release)
    _apply_music_label_overrides(meta, release)
    _apply_music_edition_override(meta, release)


def _discogs_argument_values(meta: Meta) -> tuple[tuple[Any, str, str], ...]:
    return (
        (
            meta.music_discogs_release_id,
            "release",
            "--music-discogs-release-id",
        ),
        (meta.music_discogs_master_id, "master", "--music-discogs-master-id"),
        (meta.music_discogs_id, "release", "--music-discogs-id"),
    )


def _parse_discogs_argument(
    value: Any, default_kind: str, argument: str
) -> tuple[str, str] | None:
    if not _optional_text(value):
        return None
    reference = DiscogsEnricher.parse_reference(value, default_kind)
    if reference:
        return reference
    logger.warning(
        f"[yellow]MUSIC: ignoring invalid {argument} value; use a positive Discogs ID, URL, release/ID or master/ID.[/yellow]"
    )
    return None


def _remember_discogs_reference(
    identifiers: dict[str, str], reference: tuple[str, str]
) -> None:
    kind, identifier = reference
    if kind in identifiers and not identifiers[kind]:
        identifiers[kind] = identifier


def _publish_discogs_identifier(
    release: Any, kind: str, identifier: str
) -> None:
    if not identifier:
        return
    field = f"discogs_{kind}"
    release.external_ids[field] = identifier
    release.set_field(field, identifier, MetadataSource.USER, 1.0, force=True)


def _discogs_ids(meta: Meta, release: Any) -> tuple[str, str]:
    """Resolve explicit Discogs arguments without guessing a title search."""
    identifiers = {"release": "", "master": ""}
    for value, default_kind, argument in _discogs_argument_values(meta):
        reference = _parse_discogs_argument(value, default_kind, argument)
        if reference is not None:
            _remember_discogs_reference(identifiers, reference)
    _publish_discogs_identifier(release, "release", identifiers["release"])
    _publish_discogs_identifier(release, "master", identifiers["master"])
    return identifiers["release"], identifiers["master"]


def _discogs_catalogue(release: Any) -> Any:
    directory = release.get("directory_catalogue_number", "")
    if directory:
        return directory
    return release.get(
        "release_catalogue_number", release.get("catalogue_number", "")
    )


def _filter_discogs_matches(
    release: Any, matches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    media_matches = DiscogsEnricher.filter_releases_by_media(
        matches, release.get("media", "")
    )
    if len(media_matches) != len(matches):
        logger.info(
            f"[cyan]MUSIC: filtered Discogs matches by {release.get('media')} media ({len(matches)} -> {len(media_matches)}).[/cyan]"
        )
    catalogue_matches = DiscogsEnricher.filter_releases_by_catalogue(
        media_matches, _discogs_catalogue(release)
    )
    if len(catalogue_matches) != len(media_matches):
        logger.info(
            f"[cyan]MUSIC: filtered Discogs matches by catalogue number ({len(media_matches)} -> {len(catalogue_matches)}).[/cyan]"
        )
    return catalogue_matches


def _single_discogs_identifier(matches: list[dict[str, Any]]) -> str:
    if len(matches) != 1:
        return ""
    identifier = str(matches[0].get("id", ""))
    if not identifier.isdigit():
        return ""
    logger.info(
        f"[cyan]MUSIC: found one exact Discogs release match ({identifier}).[/cyan]"
    )
    return identifier


def _discogs_candidate_details(candidate: dict[str, Any]) -> str:
    values = [
        _optional_text(candidate.get(key))
        for key in ("year", "country", "catno")
    ]
    return " / ".join(filter(None, values))


def _log_discogs_choices(matches: list[dict[str, Any]]) -> None:
    logger.info(
        "[bold yellow]Multiple exact Discogs releases found; select one or 0 to skip:[/bold yellow]"
    )
    for index, candidate in enumerate(matches, 1):
        details = _discogs_candidate_details(candidate)
        detail_text = f"({details})" if details else ""
        logger.info(
            f"[cyan]{index}.[/cyan] {candidate.get('title', '')} {detail_text} [dim]ID: {candidate.get('id', '')}[/dim]"
        )


def _parse_discogs_choice(
    choice: str, matches: list[dict[str, Any]]
) -> tuple[bool, str]:
    if choice in {"", "0"}:
        return True, ""
    if not choice.isdigit():
        return False, ""
    index = int(choice)
    if not 1 <= index <= len(matches):
        return False, ""
    identifier = str(matches[index - 1].get("id", ""))
    return identifier.isdigit(), identifier if identifier.isdigit() else ""


def _prompt_discogs_choice(matches: list[dict[str, Any]]) -> str:
    while True:
        try:
            choice = _optional_text(
                cli_ui.ask_string(
                    f"Discogs release (1-{len(matches)}, 0 to skip): "
                )
            )
        except EOFError, KeyboardInterrupt:
            logger.info(
                "[yellow]MUSIC: Discogs selection cancelled; skipping Discogs.[/yellow]"
            )
            return ""
        valid, identifier = _parse_discogs_choice(choice, matches)
        if valid:
            return identifier
        logger.info(
            "[red]Invalid Discogs selection. Enter a listed number or 0.[/red]"
        )


async def _find_discogs_release(meta: Meta, release: Any, token: str) -> str:
    """Find an exact Discogs pressing, never silently resolving ambiguity."""
    matches = await DiscogsEnricher(
        token=token, base_dir=str(meta.base_dir or "")
    ).find_exact_releases(
        str(release.get("artist", "")), str(release.get("album", ""))
    )
    matches = _filter_discogs_matches(release, matches)
    identifier = _single_discogs_identifier(matches)
    if identifier:
        return identifier
    if not matches:
        logger.info(
            "[yellow]MUSIC: no exact Discogs release match found.[/yellow]"
        )
        return ""
    if meta.unattended:
        logger.info(
            "[yellow]MUSIC: multiple exact Discogs release matches in unattended mode; skipping Discogs.[/yellow]"
        )
        return ""
    _log_discogs_choices(matches)
    return _prompt_discogs_choice(matches)


def _music_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("DEFAULT", {})
    if not isinstance(settings, dict):
        return {}
    return cast(dict[str, Any], settings)


def _music_enrichment_enabled(meta: Meta, settings: dict[str, Any]) -> bool:
    if meta.music_enrichment is not None:
        return bool(meta.music_enrichment)
    return bool(settings.get("music_enrichment_enabled", False))


async def _enrich_musicbrainz_if_enabled(
    meta: Meta, release: MusicRelease, settings: dict[str, Any]
) -> None:
    if not _music_enrichment_enabled(meta, settings):
        return
    await MusicBrainzEnricher(base_dir=str(meta.base_dir or "")).enrich(
        release
    )


def _append_music_validation_warnings(release: MusicRelease) -> None:
    issues = MusicValidator().validate(release)
    release.warnings.extend(
        f"{issue.level}: {issue.message}" for issue in issues
    )


def _music_track_size(track: Any) -> int:
    path = Path(track.path)
    if not path.is_file():
        return 0
    return path.stat().st_size


async def _export_music_mediainfo(meta: Meta, release: MusicRelease) -> None:
    if meta.edit:
        return
    if not release.tracks:
        return
    try:
        largest_track = max(release.tracks, key=_music_track_size)
        from src.integrations.media.media_info_export import export_info

        meta.mediainfo = await export_info(
            largest_track.path,
            meta.isdir,
            meta.uuid,
            meta.base_dir,
            is_dvd=False,
        )
    except MediaInfoError as error:
        logger.warning(
            f"[yellow]MediaInfo could not inspect music release: {error}[/yellow]"
        )
        logger.debug(error.debug_details)
        meta.mediainfo = {}
    except Exception as error:
        logger.warning(
            f"[yellow]MediaInfo export failed for music: {error}[/yellow]"
        )
        meta.mediainfo = {}


async def gather_music_prep(meta: Meta, config: dict[str, Any]) -> None:
    """Analyze a local release and publish a JSON-safe music snapshot into meta."""
    source_path = str(meta.path or "").strip()
    if not source_path:
        raise ReleasePathMissingError(
            "MUSIC preparation requires a release path"
        )
    release = MusicReleaseAnalyzer().analyze(source_path)
    _apply_music_cli_overrides(meta, release)
    await _enrich_musicbrainz_if_enabled(
        meta, release, _music_settings(config)
    )
    _append_music_validation_warnings(release)
    _sync_release_to_meta(meta, release)
    await prepare_music_cover(meta, release)
    await _export_music_mediainfo(meta, release)
    await _write_music_release_snapshot(meta, release)


def _discogs_release_for_enrichment(meta: Meta) -> MusicRelease | None:
    if meta.category != "MUSIC":
        return None
    if not meta.music_discogs_enabled:
        return None
    if not isinstance(meta.music_release, dict):
        return None
    return MusicRelease.from_dict(meta.music_release)


def _existing_discogs_identifier(release: MusicRelease, kind: str) -> str:
    reference = DiscogsEnricher.parse_reference(
        release.external_ids.get(f"discogs_{kind}", ""), kind
    )
    if reference is None:
        return ""
    return reference[1] if reference[0] == kind else ""


async def _resolve_discogs_identifiers(
    meta: Meta, release: MusicRelease, token: str
) -> tuple[str, str]:
    release_id, master_id = _discogs_ids(meta, release)
    if not release_id:
        release_id = _existing_discogs_identifier(release, "release")
    if not master_id:
        master_id = _existing_discogs_identifier(release, "master")
    if not release_id:
        release_id = await _find_discogs_release(meta, release, token)
    return release_id, master_id


async def enrich_music_from_discogs(
    meta: Meta, config: dict[str, Any]
) -> bool:
    """Resolve Discogs after exact tracker metadata has enriched the release."""
    release = _discogs_release_for_enrichment(meta)
    if release is None:
        return False
    token = str(_music_settings(config).get("music_discogs_token", ""))
    release_id, master_id = await _resolve_discogs_identifiers(
        meta, release, token
    )
    if not release_id and not master_id:
        return False
    await DiscogsEnricher(
        token=token, base_dir=str(meta.base_dir or "")
    ).enrich(release, release_id=release_id, master_id=master_id)
    _sync_release_to_meta(meta, release)
    await _write_music_release_snapshot(meta, release)
    return True


def _release_year(release: MusicRelease, current: int | None) -> int | None:
    value = str(release.get("year", ""))
    return int(value) if value.isdigit() else current


def _release_source(release: MusicRelease, current: str | None) -> str | None:
    value = str(release.get("media", current or ""))
    return value if value else current


def _release_genres(release: MusicRelease, current: list[str]) -> list[str]:
    value = release.get("genres", current)
    return list(value) if isinstance(value, list) else current


def _sync_release_to_meta(meta: Meta, release: MusicRelease) -> None:
    """Publish the tracker-neutral release model to the shared metadata object."""
    meta.music_release = release.to_dict()
    meta.artist = str(release.get("artist", meta.artist))
    meta.title = str(release.get("album", meta.title))
    meta.year = _release_year(release, meta.year)
    meta.format = str(release.get("format", meta.format))
    meta.source = _release_source(release, meta.source)
    meta.scene = bool(release.get("scene", meta.scene))
    meta.genres = _release_genres(release, meta.genres)
    meta.audio = f"{meta.format} / {release.get('disc_count', 1)} disc(s) / {release.get('track_count', 0)} track(s)"
    meta.filelist = [track.path for track in release.tracks]
    meta.name_notag = _music_name(release)
    meta.name = meta.name_notag
    meta.clean_name = meta.name_notag


async def _write_music_release_snapshot(
    meta: Meta, release: MusicRelease
) -> None:
    """Persist the current release snapshot for review and later upload stages."""
    path = music_release_snapshot_path(meta.base_dir, str(meta.uuid))
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as file:
        await file.write(
            json.dumps(release.to_dict(), indent=2, cls=PathAwareEncoder)
        )


def _set_tracker_field(
    release: MusicRelease, name: str, value: Any, confidence: float
) -> None:
    """Use tracker data only to fill gaps; local evidence and CLI input win."""
    existing = release.fields.get(name)
    protected = {
        MetadataSource.USER,
        MetadataSource.FILE_TAG,
        MetadataSource.AUXILIARY,
    }
    effective_confidence = (
        0.0 if existing and existing.source in protected else confidence
    )
    release.set_field(
        name, value, MetadataSource.TRACKER, effective_confidence
    )


def _orpheus_person_name(person: Any) -> str:
    if not isinstance(person, dict):
        return ""
    typed_person = cast(dict[str, Any], person)
    return _optional_text(typed_person.get("name"))


def _orpheus_people(group: dict[str, Any], role: str) -> list[str]:
    music_info = group.get("musicInfo")
    if not isinstance(music_info, dict):
        return []
    typed_music_info = cast(dict[str, Any], music_info)
    people = typed_music_info.get(role)
    if not isinstance(people, list):
        return []
    names = map(_orpheus_person_name, people)
    return list(dict.fromkeys(filter(None, names)))


def _orpheus_identifier(meta: Meta) -> str:
    identifier = _optional_text(meta.get_tracker_id("ORPHEUS"))
    if meta.category != "MUSIC":
        return ""
    if not identifier.isdigit():
        return ""
    if not isinstance(meta.music_release, dict):
        return ""
    return identifier


def _orpheus_payload(
    result: Any, identifier: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not isinstance(result, dict):
        logger.info(
            f"[yellow]MUSIC: Orpheus metadata was unavailable for torrent {identifier}.[/yellow]"
        )
        return None
    typed_result = cast(dict[str, Any], result)
    group = typed_result.get("group")
    torrent = typed_result.get("torrent")
    if not isinstance(group, dict):
        return None
    if not isinstance(torrent, dict):
        return None
    return cast(dict[str, Any], group), cast(dict[str, Any], torrent)


def _apply_orpheus_people(
    release: MusicRelease, group: dict[str, Any]
) -> None:
    artists = _orpheus_people(group, "artists")
    composers = _orpheus_people(group, "composers")
    if artists:
        _set_tracker_field(release, "artists", artists, 0.93)
        _set_tracker_field(release, "artist", " & ".join(artists), 0.93)
    if composers:
        _set_tracker_field(release, "composers", composers, 0.9)


def _apply_orpheus_core_fields(
    release: MusicRelease, group: dict[str, Any], torrent: dict[str, Any]
) -> None:
    _set_tracker_field(
        release, "album", _optional_text(group.get("name")), 0.93
    )
    _set_tracker_field(
        release, "year", _optional_text(group.get("year")), 0.84
    )
    _set_tracker_field(
        release,
        "release_type",
        _optional_text(group.get("releaseTypeName")),
        0.88,
    )
    _set_tracker_field(release, "genres", group.get("tags", []), 0.78)
    _set_tracker_field(
        release, "media", _optional_text(torrent.get("media")), 0.9
    )
    _set_tracker_field(
        release,
        "release_year",
        _optional_text(torrent.get("remasterYear")),
        0.89,
    )


def _apply_orpheus_remaster_fields(
    release: MusicRelease, torrent: dict[str, Any]
) -> None:
    remaster_title = _optional_text(torrent.get("remasterTitle"))
    if remaster_title:
        _set_tracker_field(release, "edition", remaster_title, 0.9)
        _set_tracker_field(
            release,
            "edition_year",
            _optional_text(torrent.get("remasterYear")),
            0.89,
        )
    _set_tracker_field(
        release,
        "release_label",
        _optional_text(torrent.get("remasterRecordLabel")),
        0.91,
    )
    _set_tracker_field(
        release,
        "release_catalogue_number",
        _optional_text(torrent.get("remasterCatalogueNumber")),
        0.91,
    )
    _set_tracker_field(
        release,
        "orpheus_encoding",
        _optional_text(torrent.get("encoding")),
        0.9,
    )


def _apply_orpheus_ids(
    release: MusicRelease,
    group: dict[str, Any],
    identifier: str,
    base_url: str,
) -> None:
    release.external_ids.setdefault("orpheus_torrent", identifier)
    group_id = _optional_text(group.get("id"))
    if not group_id:
        return
    release.external_ids.setdefault("orpheus_group", group_id)
    release.external_ids.setdefault(
        "orpheus_url",
        f"{base_url}/torrents.php?id={group_id}&torrentid={identifier}",
    )


def _apply_orpheus_wiki_ids(release: MusicRelease, wiki: str) -> None:
    patterns = (
        ("musicbrainz_release", r"musicbrainz\.org/release/([0-9a-f-]{36})"),
        ("discogs_release", r"discogs\.com/release/(\d+)"),
        ("discogs_master", r"discogs\.com/master/(\d+)"),
    )
    for key, pattern in patterns:
        match = re.search(pattern, wiki, flags=re.IGNORECASE)
        if match:
            release.external_ids.setdefault(key, match.group(1))


def _apply_orpheus_cover(
    meta: Meta, release: MusicRelease, group: dict[str, Any]
) -> None:
    if meta.artwork_url or meta.artwork_path:
        return
    cover = _optional_text(group.get("wikiImage"))
    if not cover.startswith(("https://", "http://")):
        return
    meta.artwork_url = cover
    _set_tracker_field(release, "cover_url", cover, 0.75)


async def enrich_music_from_orpheus(
    meta: Meta, config: dict[str, Any]
) -> bool:
    """Enrich an analyzed MUSIC release from an explicitly known Orpheus torrent."""
    identifier = _orpheus_identifier(meta)
    if not identifier:
        return False

    from src.integrations.trackers.orpheus import Orpheus

    orpheus = Orpheus(config)
    payload = _orpheus_payload(
        await orpheus.get_torrent(identifier, meta), identifier
    )
    if payload is None:
        return False
    group, torrent = payload
    release = MusicRelease.from_dict(cast(dict[str, Any], meta.music_release))
    _apply_orpheus_people(release, group)
    _apply_orpheus_core_fields(release, group, torrent)
    _apply_orpheus_remaster_fields(release, torrent)
    _apply_orpheus_ids(release, group, identifier, orpheus.base_url)
    _apply_orpheus_wiki_ids(release, _optional_text(group.get("wikiBBcode")))
    _apply_orpheus_cover(meta, release, group)
    _sync_release_to_meta(meta, release)
    await _write_music_release_snapshot(meta, release)
    logger.info(
        f"[green]MUSIC: enriched metadata from Orpheus torrent {identifier}.[/green]"
    )
    return True


def _music_technical_piece(release: Any) -> str:
    media = str(release.get("media", ""))
    format_name = str(release.get("format", ""))
    if not media and not format_name:
        return ""
    return f"[{media} {format_name}]".strip()


def _music_name(release: Any) -> str:
    pieces = [
        str(release.get("artist", "")),
        "-",
        str(release.get("album", "")),
    ]
    year = str(release.get("year", ""))
    if year:
        pieces.append(f"[{year}]")
    pieces.append(_music_technical_piece(release))
    return " ".join(filter(None, pieces)).replace("  ", " ").strip()
