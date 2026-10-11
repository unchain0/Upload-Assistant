# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import cli_ui
import guessit

from src.domain_models.release import Meta
from src.engines.region_mapping import get_distributor
from src.services.runtime_support import logger, prompt_in_thread

guessit_module: Any = cast(Any, guessit)
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]
EditionInfo = dict[str, Any]
EditionDetails = dict[str, EditionInfo]
EditionCandidate = dict[str, Any]


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


def _has_release_token(value: str, token: str) -> bool:
    """Return whether a scene-release marker appears as its own token."""
    return (
        re.search(
            rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])",
            value,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _strip_release_tokens(value: str) -> str:
    """Remove standalone release markers while preserving adjacent text."""
    return re.sub(
        r"(?<![A-Z0-9])(?:REPACK\d?|RERIP|PROPER\d?)(?![A-Z0-9])",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


def _edition_context(meta: Meta) -> tuple[EditionDetails, int]:
    imdb_info = cast(dict[str, Any], meta.imdb_info)
    details = cast(EditionDetails, imdb_info.get("edition_details", {}))
    count_value = imdb_info.get("edition_count", len(details))
    try:
        return details, int(count_value)
    except TypeError, ValueError:
        return details, len(details)


def _should_match_imdb(
    meta: Meta,
    details: EditionDetails,
    edition_count: int,
    manual_edition: str | list[str],
) -> bool:
    return (
        meta.category == "MOVIE"
        and not meta.anime
        and bool(details)
        and edition_count > 1
        and not manual_edition
    )


def _interactive(meta: Meta) -> bool:
    return not meta.unattended or meta.unattended_confirm


def _edition_attributes(info: EditionInfo) -> list[Any]:
    attributes = info.get("attributes")
    if isinstance(attributes, list):
        return cast(list[Any], attributes)
    return []


def _general_duration(meta: Meta) -> Any | None:
    tracks = cast(
        list[dict[str, Any]],
        meta.mediainfo.get("media", {}).get("track", []) or [],
    )
    general = next(
        (track for track in tracks if track.get("@type") == "General"),
        None,
    )
    if general is None:
        return None
    return general.get("Duration")


def _candidate_difference(candidate: EditionCandidate) -> float:
    return float(candidate.get("difference", 0) or 0)


def _closest(candidates: list[EditionCandidate]) -> EditionCandidate:
    return min(candidates, key=_candidate_difference)


def _valid_choice(choice: str, count: int) -> bool:
    if not choice.strip() or not choice.isdigit():
        return False
    return 1 <= int(choice) <= count


def _duration_candidate(
    info: EditionInfo, media_seconds: float, leeway_seconds: float
) -> EditionCandidate | None:
    edition_seconds = float(info.get("seconds", 0) or 0)
    difference = abs(media_seconds - edition_seconds)
    if difference > leeway_seconds:
        return None
    formatted = format_duration(edition_seconds)
    attributes = _edition_attributes(info)
    logger.debug(
        f"[green]Potential match: {info.get('display_name', '')} - duration {formatted}, difference: {format_duration(difference)}[/green]"
    )
    if not attributes:
        logger.debug(
            "[yellow]Edition without attributes are theatrical editions and skipped[/yellow]"
        )
        return None
    return {
        "name": " ".join(smart_title(str(attr)) for attr in attributes),
        "display_name": str(info.get("display_name", "")),
        "has_attributes": True,
        "minutes": info.get("minutes"),
        "difference": difference,
        "formatted_duration": formatted,
    }


def _duration_candidates(
    details: EditionDetails, media_seconds: float, leeway_seconds: float
) -> list[EditionCandidate]:
    candidates: list[EditionCandidate] = []
    for info in details.values():
        candidate = _duration_candidate(info, media_seconds, leeway_seconds)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


async def _prompt_duration_candidate(
    candidates: list[EditionCandidate], formatted_duration: str
) -> EditionCandidate:
    logger.info(
        f"[yellow]Media file duration {formatted_duration} matches multiple editions:[/yellow]"
    )
    for index, candidate in enumerate(candidates):
        logger.info(
            f"[yellow]{index + 1}. [green]{candidate.get('name', '')} ({candidate.get('display_name', '')}, duration: {candidate.get('formatted_duration', '')}, diff: {format_duration(_candidate_difference(candidate))})[/yellow]"
        )
    try:
        choice = (
            await prompt_in_thread(
                cli_ui.ask_string,
                f"Select edition number (1-{len(candidates)}) or press Enter to use the closest match:",
                default="",
            )
            or ""
        )
        if _valid_choice(choice, len(candidates)):
            return candidates[int(choice) - 1]
    except Exception as exc:
        logger.error(
            f"[red]Error processing selection: {exc}. Using closest match.[/red]"
        )
    selected = _closest(candidates)
    logger.info(
        f"[yellow]Using closest match: {selected.get('name', '')}[/yellow]"
    )
    return selected


async def _select_duration_candidate(
    meta: Meta,
    candidates: list[EditionCandidate],
    formatted_duration: str,
) -> EditionCandidate:
    if _interactive(meta):
        return await _prompt_duration_candidate(candidates, formatted_duration)
    selected = _closest(candidates)
    logger.info(
        f"[yellow]Multiple matches found in unattended mode. Using closest match: {selected.get('name', '')}[/yellow]"
    )
    return selected


def _parsed_duration(duration_value: Any) -> float | None:
    try:
        return float(duration_value)
    except (ValueError, TypeError) as exc:
        logger.info(f"[yellow]Error parsing duration: {exc}[/yellow]")
        return None


def _candidate_edition_name(candidate: EditionCandidate) -> str:
    if not candidate.get("has_attributes"):
        return ""
    return str(candidate.get("name", ""))


async def _duration_edition(meta: Meta, details: EditionDetails) -> str:
    duration_value = _general_duration(meta)
    if not duration_value:
        return ""
    media_seconds = _parsed_duration(duration_value)
    if media_seconds is None:
        return ""
    formatted = format_duration(media_seconds)
    logger.debug(
        f"[cyan]Found media duration: {formatted} ({media_seconds} seconds)[/cyan]"
    )
    leeway_seconds = 50.0
    candidates = _duration_candidates(details, media_seconds, leeway_seconds)
    if not candidates:
        logger.debug(
            f"[yellow]No matching editions found within {int(leeway_seconds)} seconds of media duration[/yellow]"
        )
        return ""
    selected = candidates[0]
    if len(candidates) > 1:
        selected = await _select_duration_candidate(
            meta, candidates, formatted
        )
    edition = _candidate_edition_name(selected)
    logger.info(
        f"[bold green]Setting edition from duration match: {edition}[/bold green]"
    )
    return edition


def _disc_playlists(meta: Meta) -> list[dict[str, Any]]:
    key = "playlists" if _interactive(meta) else "all_valid_playlists"
    playlists: list[dict[str, Any]] = []
    for disc in cast(list[dict[str, Any]], meta.discs):
        values = disc.get(key)
        if isinstance(values, list):
            playlists.extend(cast(list[dict[str, Any]], values))
    return playlists


def _playlist_candidate(
    info: EditionInfo, playlist_duration: float, leeway_seconds: float
) -> EditionCandidate | None:
    edition_seconds = float(info.get("seconds", 0) or 0)
    difference = abs(playlist_duration - edition_seconds)
    if difference > leeway_seconds:
        return None
    attributes = _edition_attributes(info)
    name = (
        " ".join(smart_title(str(attr)) for attr in attributes)
        if attributes
        else f"{info.get('minutes')} Minute Version (Theatrical)"
    )
    return {
        "name": name,
        "display_name": str(info.get("display_name", "")),
        "has_attributes": bool(attributes),
        "minutes": info.get("minutes"),
        "difference": difference,
    }


def _playlist_candidates(
    details: EditionDetails, duration: float, leeway_seconds: float = 50.0
) -> list[EditionCandidate]:
    candidates: list[EditionCandidate] = []
    for info in details.values():
        candidate = _playlist_candidate(info, duration, leeway_seconds)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _log_playlist_candidates(
    candidates: list[EditionCandidate],
    playlist_edition: str,
    playlist_file: str,
    formatted_duration: str,
) -> None:
    logger.info(
        f"[yellow]Playlist edition [green]{playlist_edition} [yellow]using file [green]{playlist_file} [yellow]with duration [green]{formatted_duration} [yellow]matches multiple editions:[/yellow]"
    )
    for index, candidate in enumerate(candidates):
        logger.info(
            f"[yellow]{index + 1}. [green]{candidate['name']} ({candidate['display_name']}, diff: {_candidate_difference(candidate):.2f} seconds)"
        )


async def _prompt_playlist_candidate(
    candidates: list[EditionCandidate],
    playlist_edition: str,
    playlist_file: str,
    formatted_duration: str,
) -> str | EditionCandidate:
    _log_playlist_candidates(
        candidates, playlist_edition, playlist_file, formatted_duration
    )
    try:
        choice = (
            await prompt_in_thread(
                cli_ui.ask_string,
                f"Select edition number (1-{len(candidates)}), press e to use playlist edition or press Enter to use the closest match:",
                default="",
            )
            or ""
        )
        if _valid_choice(choice, len(candidates)):
            return candidates[int(choice) - 1]
        if choice.strip().lower() == "e":
            return playlist_edition
        selected = _closest(candidates)
        logger.info(
            f"[yellow]Using closest match: {selected['name']}[/yellow]"
        )
        return selected
    except Exception as exc:
        logger.error(
            f"[red]Error processing selection: {exc}. Using closest match.[/red]"
        )
        return _closest(candidates)


async def _select_playlist_candidate(
    meta: Meta,
    candidates: list[EditionCandidate],
    playlist_edition: str,
    playlist_file: str,
    formatted_duration: str,
) -> str | EditionCandidate:
    if _interactive(meta):
        return await _prompt_playlist_candidate(
            candidates, playlist_edition, playlist_file, formatted_duration
        )
    logger.info(
        f"[yellow]Playlist edition [green]{playlist_edition} [yellow]using file [green]{playlist_file} [yellow]with duration [green]{formatted_duration} [yellow]matches multiple editions, but unattended mode is enabled. Using closest match.[/yellow]"
    )
    return _closest(candidates)


def _append_candidate(
    candidate: EditionCandidate,
    with_attributes: list[str],
    without_attributes: list[str],
) -> None:
    if candidate.get("has_attributes"):
        name = str(candidate.get("name", ""))
        if name not in with_attributes:
            with_attributes.append(name)
            logger.info(
                f"[green]Added edition with attributes: {name}[/green]"
            )
        return
    without_attributes.append(str(candidate.get("minutes", "")))
    logger.info(
        f"[yellow]Added edition without attributes: {candidate.get('name', '')}[/yellow]"
    )


def _consume_playlist_string(
    selection: str,
    candidates: list[EditionCandidate],
    with_attributes: list[str],
    without_attributes: list[str],
) -> None:
    normalized = selection.strip().lower()
    if not normalized:
        logger.info(
            "[yellow]Empty playlist edition, using closest match.[/yellow]"
        )
        _append_candidate(
            _closest(candidates), with_attributes, without_attributes
        )
        return
    if normalized in {"theatrical", "theater", "theatre"}:
        logger.info(
            f"[yellow]Playlist edition '{selection}' is theatrical, treating as non-attribute edition.[/yellow]"
        )
        without_attributes.append(str(_closest(candidates).get("minutes", "")))
        return
    stripped = selection.strip()
    if stripped not in with_attributes:
        with_attributes.append(stripped)
        logger.info(f"[green]Using playlist edition: {selection}[/green]")
    else:
        logger.info(
            f"[yellow]Playlist edition '{selection}' already added, skipping duplicate.[/yellow]"
        )


def _consume_playlist_selection(
    selection: str | EditionCandidate,
    candidates: list[EditionCandidate],
    with_attributes: list[str],
    without_attributes: list[str],
) -> None:
    if isinstance(selection, str):
        _consume_playlist_string(
            selection, candidates, with_attributes, without_attributes
        )
        return
    _append_candidate(selection, with_attributes, without_attributes)


def _playlist_text(playlist: dict[str, Any], key: str) -> str:
    return str(playlist.get(key) or "")


def _consume_single_playlist_candidate(
    candidate: EditionCandidate,
    playlist_edition: str,
    with_attributes: list[str],
    without_attributes: list[str],
) -> None:
    logger.debug(
        f"[green]Playlist {playlist_edition} matches edition: {candidate['display_name']} {candidate['name']}[/green]"
    )
    _append_candidate(candidate, with_attributes, without_attributes)


async def _process_playlist(
    meta: Meta,
    playlist: dict[str, Any],
    details: EditionDetails,
    with_attributes: list[str],
    without_attributes: list[str],
) -> None:
    duration_value = playlist.get("duration")
    if not duration_value:
        return
    duration = float(duration_value)
    formatted = format_duration(duration)
    logger.debug(
        f"[cyan]Checking playlist duration: {formatted} seconds[/cyan]"
    )
    candidates = _playlist_candidates(details, duration)
    if not candidates:
        return
    playlist_edition = _playlist_text(playlist, "edition")
    playlist_file = _playlist_text(playlist, "file")
    if len(candidates) == 1:
        _consume_single_playlist_candidate(
            candidates[0],
            playlist_edition,
            with_attributes,
            without_attributes,
        )
        return
    selection = await _select_playlist_candidate(
        meta, candidates, playlist_edition, playlist_file, formatted
    )
    _consume_playlist_selection(
        selection, candidates, with_attributes, without_attributes
    )


def _combined_playlist_editions(
    with_attributes: list[str], without_attributes: list[str]
) -> list[str]:
    if with_attributes and without_attributes:
        logger.debug(
            "[cyan]Adding 'Theatrical' label because we have both attribute and non-attribute editions[/cyan]"
        )
        return [*with_attributes, "Theatrical"]
    if with_attributes:
        logger.debug("[cyan]Using only editions with attributes[/cyan]")
        return with_attributes
    logger.debug("[cyan]No useful editions found[/cyan]")
    return []


def _format_playlist_editions(editions: list[str]) -> str:
    if not editions:
        return ""
    if len(editions) == 1:
        return editions[0]
    unique = list(set(editions))
    if "Theatrical" in unique:
        unique.remove("Theatrical")
        unique = ["Theatrical", *sorted(unique)]
    if len(unique) == 1:
        return unique[0]
    return f"{len(unique)}in1 " + " / ".join(unique)


async def _bluray_edition(meta: Meta, details: EditionDetails) -> str:
    logger.debug("[cyan]Checking BDMV playlists for edition matches...[/cyan]")
    playlists = _disc_playlists(meta)
    logger.debug(
        f"[cyan]Found {len(playlists)} playlists to check against IMDb editions[/cyan]"
    )
    with_attributes: list[str] = []
    without_attributes: list[str] = []
    for playlist in playlists:
        await _process_playlist(
            meta, playlist, details, with_attributes, without_attributes
        )
    edition = _format_playlist_editions(
        _combined_playlist_editions(with_attributes, without_attributes)
    )
    if edition:
        logger.debug(
            f"[bold green]Setting edition from BDMV playlist matches: {edition}[/bold green]"
        )
    return edition


async def _matched_imdb_edition(meta: Meta, details: EditionDetails) -> str:
    if meta.is_disc == "BDMV":
        if not meta.discs:
            return ""
        return await _bluray_edition(meta, details)
    if not meta.mediainfo.get("media", {}).get("track"):
        return ""
    return await _duration_edition(meta, details)


def _discard_unusable_edition(edition: str) -> str:
    if not edition:
        return ""
    if edition.lower() in {"cut", "approximate"} or len(edition) < 6:
        return ""
    return edition


def _normalize_automatic_edition(edition: str) -> str:
    edition = _discard_unusable_edition(edition)
    if not edition:
        return ""
    if "edition" in edition.lower():
        edition = re.sub(
            r"\bedition\b", "", edition, flags=re.IGNORECASE
        ).strip()
    if "extended" in edition.lower():
        return "Extended"
    return edition


def _guess_release_group(guess: dict[str, Any]) -> str:
    tag_value = guess.get("release_group", "NOGROUP")
    if isinstance(tag_value, list):
        return " ".join(str(tag) for tag in cast(list[Any], tag_value))
    return str(tag_value)


def _guess_edition_value(
    guess: dict[str, Any], bdinfo: dict[str, Any] | None
) -> Any:
    if bdinfo is not None:
        try:
            return guessit_fn(bdinfo["label"]).get("edition", "")
        except Exception as exc:
            logger.debug(
                f"BDInfo Edition Guess Error: {exc}", extra={"markup": False}
            )
            return ""
    try:
        return guess.get("edition", "")
    except Exception as exc:
        logger.debug(
            f"Video Edition Guess Error: {exc}", extra={"markup": False}
        )
        return ""


def _edition_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in cast(list[Any], value))
    return str(value or "")


def _normalized_video(video: str, filelist: list[str], tag: str) -> str:
    if len(filelist) == 1:
        video = Path(video).name
    return (
        video.upper()
        .replace(".", " ")
        .replace(tag.upper(), "")
        .replace("-", " ")
    )


def _guess_missing_edition(
    video: str, bdinfo: dict[str, Any] | None, filelist: list[str]
) -> tuple[str, str]:
    if video.lower().startswith("dc"):
        video = video.lower().replace("dc", "", 1)
    guess = guessit_fn(video)
    tag = _guess_release_group(guess)
    edition = _edition_text(_guess_edition_value(guess, bdinfo))
    video = _normalized_video(video, filelist, tag)
    if "OPEN MATTE" in video.upper():
        edition += " Open Matte"
    return edition, video


def _manual_edition(manual_edition: str | list[str]) -> str:
    if isinstance(manual_edition, list):
        return " ".join(str(item) for item in manual_edition)
    return manual_edition


_REPACK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("REPACK", ("REPACK", "V2")),
    ("REPACK2", ("REPACK2", "V3")),
    ("REPACK3", ("REPACK3", "V4")),
    ("PROPER", ("PROPER",)),
    ("PROPER2", ("PROPER2",)),
    ("PROPER3", ("PROPER3",)),
    ("RERIP", ("RERIP",)),
)
_RELEASE_MARKERS = {
    "repack",
    "repack2",
    "repack3",
    "proper",
    "proper2",
    "proper3",
    "rerip",
}


def _detect_repack(release_text: str) -> str:
    repack = ""
    for value, tokens in _REPACK_RULES:
        if any(_has_release_token(release_text, token) for token in tokens):
            repack = value
    return repack


def _should_strip_markers(manual_edition: str) -> bool:
    if not manual_edition:
        return True
    return all(
        token not in _RELEASE_MARKERS
        for token in manual_edition.strip().lower().split()
    )


def _hybrid(meta: Meta, video: str, edition: str) -> bool:
    if meta.webdv:
        return bool(meta.webdv)
    return "HYBRID" in video.upper() or "HYBRID" in edition.upper()


def _bad_edition_terms(meta: Meta, distributor: str) -> list[str]:
    bad = ["internal", "limited", "retail", "version", "remastered"]
    if distributor and meta.is_disc:
        bad.append(distributor.lower())
        meta.distributor = distributor
    return bad


def _strip_bad_terms(edition: str, bad: list[str]) -> str:
    if not any(term.lower() in edition.lower() for term in bad):
        return edition
    edition = re.sub(
        r"\b(?:" + "|".join(bad) + r")\b",
        "",
        edition,
        flags=re.IGNORECASE,
    ).strip()
    while "  " in edition:
        edition = edition.replace("  ", " ")
    return edition


async def _clean_distributor(edition: str, meta: Meta) -> str:
    if not edition:
        return edition
    distributor = await get_distributor(edition)
    edition = _strip_bad_terms(edition, _bad_edition_terms(meta, distributor))
    if edition:
        edition = edition.strip()
        logger.debug(f"Final Edition: {edition}")
    return edition


async def get_edition(
    video: str,
    bdinfo: dict[str, Any] | None,
    filelist: list[str],
    manual_edition: str | list[str],
    meta: Meta,
) -> tuple[str, str, bool]:
    details, edition_count = _edition_context(meta)
    edition = ""
    if _should_match_imdb(meta, details, edition_count, manual_edition):
        edition = await _matched_imdb_edition(meta, details)
    edition = _normalize_automatic_edition(edition)
    if not edition:
        edition, video = _guess_missing_edition(video, bdinfo, filelist)
    manual = _manual_edition(manual_edition)
    if manual:
        edition = manual
    edition = edition.replace(",", " ")
    repack = _detect_repack(f"{video} {edition}")
    if _should_strip_markers(manual):
        edition = _strip_release_tokens(edition)
    hybrid = _hybrid(meta, video, edition)
    edition = await _clean_distributor(edition, meta)
    return edition, repack, hybrid


def format_duration(seconds: float) -> str:
    """Convert seconds to a human-readable HH:MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def smart_title(s: str) -> str:
    """Custom title function that doesn't capitalize after apostrophes."""
    result = s.title()
    return re.sub(
        r"(\w)'(\w)",
        lambda match: f"{match.group(1)}'{match.group(2).lower()}",
        result,
    )
