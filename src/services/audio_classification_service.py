# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Audio category classifier for distinguishing MUSIC, PODCAST, and AUDIOBOOK releases."""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import mutagen

from src.services.book_preparation import BOOK_EXTENSIONS

mutagen_module: Any = cast(Any, mutagen)

AUDIOBOOK_CONTAINER_EXTENSIONS = frozenset({".m4b", ".aax", ".aaxc"})
SHARED_AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".flac",
        ".m4a",
        ".aac",
        ".ac3",
        ".dts",
        ".wav",
        ".aiff",
        ".alac",
        ".ogg",
        ".opus",
        ".ape",
        ".wv",
        ".wma",
    }
)

PODCAST_GENRES = frozenset({"podcast", "podcasts", "news & politics"})

SPOKEN_GENRES = frozenset(
    {
        "audiobook",
        "audio book",
        "audiobooks",
        "audio books",
        "spoken word",
        "spokenword",
        "speech",
        "spoken",
        "audio drama",
        "radio play",
        "story",
        "nonfiction",
        "fiction",
        "novel",
        "memoir",
        "biography",
        "lecture",
        "talk",
    }
)

MUSIC_GENRES = frozenset(
    {
        "rock",
        "pop",
        "jazz",
        "metal",
        "electronic",
        "classical",
        "hip hop",
        "hip-hop",
        "rap",
        "indie",
        "soundtrack",
        "folk",
        "blues",
        "reggae",
        "country",
        "ambient",
        "punk",
        "house",
        "techno",
        "trance",
        "disco",
        "funk",
        "soul",
        "r&b",
        "r & b",
        "alternative",
        "dance",
        "industrial",
        "instrumental",
        "heavy metal",
        "pop rock",
        "hard rock",
        "synthpop",
        "lo-fi",
        "ska",
        "grunge",
        "gospel",
        "opera",
        "symphony",
        "bluegrass",
        "new age",
    }
)

AUDIOBOOK_FILENAME_REGEX = re.compile(
    r"(?i)\b(?:chapter|part|pt|section|act|bk|book)\s*\d+|\b(?:part|ch|chapter)\d+\b|\btrack\s*\d+\s*[-_]\s*chapter\b|\b(?:audiobook|unabridged|abridged|read by|narrated by)\b"
)
MUSIC_FILENAME_REGEX = re.compile(
    r"^\s*\d{1,3}\s*[-._]\s*(?![cC]hapter|[pP]art|[bB]ook)\w+"
)
SCENE_MUSIC_RELEASE_REGEX = re.compile(
    r"(?i)(?:^|[-_.])(?:SINGLE|EP|LP|ALBUM)[-_.]+WEB(?:[-_.]|$)|[-_.]WEB[-_.](?:19|20)\d{2}[-_.][A-Z0-9]+$"
)
DATED_MUSIC_RELEASE_REGEX = re.compile(
    r"^\s*\S.*?\s+-\s+(?:19|20)\d{2}-\d{2}-\d{2}\s+-\s+\S", re.I
)

_TAG_VALUE_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("narrator", "read by", "reader"), "narrator"),
    (("author", "writer"), "author"),
    (("publisher",), "publisher"),
    (("isbn",), "isbn"),
    (("asin",), "asin"),
)
_TAG_FLAG_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("musicbrainz",), "has_musicbrainz"),
    (("discogs",), "has_discogs"),
    (("catalognumber", "catno", "label"), "has_catalog_no"),
)
VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".ts", ".avi", ".m2ts"})
EBOOK_EXTENSIONS = BOOK_EXTENSIONS - {".txt", ".html", ".htm"}


@dataclass
class AudioCategoryResult:
    category: str
    is_audiobook: bool = False
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class _ScoreCard:
    audio_count: int
    book: float = 0.0
    music: float = 0.0
    podcast: float = 0.0
    book_evidence: list[str] = field(init=False)
    music_evidence: list[str] = field(init=False)
    podcast_evidence: list[str] = field(init=False)

    def __post_init__(self) -> None:
        evidence = f"{self.audio_count} audio files detected"
        self.book_evidence = [evidence]
        self.music_evidence = [evidence]
        self.podcast_evidence = [evidence]

    def add_book(self, score: float, evidence: str) -> None:
        self.book += score
        self.book_evidence.append(evidence)

    def add_music(self, score: float, evidence: str) -> None:
        self.music += score
        self.music_evidence.append(evidence)

    def add_podcast(self, score: float, evidence: str) -> None:
        self.podcast += score
        self.podcast_evidence.append(evidence)


@dataclass
class _SampleSummary:
    genres: set[str] = field(default_factory=set)
    has_chapters: bool = False
    has_narrator: bool = False
    has_author: bool = False
    has_isbn_asin: bool = False
    has_musicbrainz: bool = False
    has_discogs: bool = False
    has_catalog_no: bool = False
    mono_count: int = 0
    low_bitrate_count: int = 0
    low_samplerate_count: int = 0
    long_track_count: int = 0


def _empty_audio_info() -> dict[str, Any]:
    return {
        "channels": 0,
        "bitrate": 0,
        "sample_rate": 0,
        "length": 0.0,
        "genres": [],
        "title": "",
        "artist": "",
        "album": "",
        "albumartist": "",
        "narrator": "",
        "author": "",
        "publisher": "",
        "isbn": "",
        "asin": "",
        "has_chapters": False,
        "has_musicbrainz": False,
        "has_discogs": False,
        "has_catalog_no": False,
        "raw_tag_text": "",
    }


def _easy_tag_values(tags: Any, key: str) -> list[Any]:
    values = tags.get(key, [])
    return cast(list[Any], values) if isinstance(values, list) else []


def _apply_easy_tags(info: dict[str, Any], tags: Any) -> None:
    genres = _easy_tag_values(tags, "genre")
    info["genres"].extend(str(value).strip() for value in genres if value)
    for key in ("title", "artist", "album", "albumartist"):
        values = _easy_tag_values(tags, key)
        if values:
            info[key] = str(values[0]).strip()


def _inspect_easy_tags(filepath: Path, info: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        audio = mutagen_module.File(str(filepath), easy=True)
        tags = getattr(audio, "tags", None)
        if tags:
            _apply_easy_tags(info, tags)


def _technical_value(technical: Any, key: str, default: Any) -> Any:
    value = getattr(technical, key, default)
    return value if value else default


def _apply_technical_info(info: dict[str, Any], audio: Any) -> None:
    technical = getattr(audio, "info", None)
    if not technical:
        return
    info["channels"] = _technical_value(technical, "channels", 0)
    info["bitrate"] = _technical_value(technical, "bitrate", 0)
    info["sample_rate"] = _technical_value(technical, "sample_rate", 0)
    info["length"] = _technical_value(technical, "length", 0.0)


def _chapter_key(key: str) -> bool:
    return key.startswith(("CHAP", "CTOC")) or "chapter" in key.lower()


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _matching_tag_targets(
    key: str, rules: tuple[tuple[tuple[str, ...], str], ...]
) -> list[str]:
    return [
        target for markers, target in rules if _contains_marker(key, markers)
    ]


def _apply_named_tag(info: dict[str, Any], key: str, value: str) -> None:
    lowered = key.lower()
    for target in _matching_tag_targets(lowered, _TAG_VALUE_FIELDS):
        info[target] = value
    for target in _matching_tag_targets(lowered, _TAG_FLAG_FIELDS):
        info[target] = True
    if "genre" in lowered and not info["genres"]:
        info["genres"].append(value)


def _tag_keys(tags: Any) -> list[Any]:
    if not hasattr(tags, "keys"):
        return []
    return list(tags.keys())


def _tag_items(tags: Any) -> list[tuple[Any, Any]]:
    if not hasattr(tags, "items"):
        return []
    return list(tags.items())


def _has_chapter_key(tags: Any) -> bool:
    return any(_chapter_key(str(key)) for key in _tag_keys(tags))


def _render_full_tag_items(info: dict[str, Any], tags: Any) -> str:
    pieces: list[str] = []
    for key, value in _tag_items(tags):
        lowered = str(key).lower()
        rendered = str(value)
        pieces.append(f"{lowered}={rendered}")
        _apply_named_tag(info, lowered, rendered)
    return " ".join(pieces).lower()


def _raw_text_has_chapter(raw_text: str) -> bool:
    return any(marker in raw_text for marker in ("chapter00", "chapter01"))


def _apply_full_tags(info: dict[str, Any], tags: Any) -> None:
    if _has_chapter_key(tags):
        info["has_chapters"] = True
    raw_text = _render_full_tag_items(info, tags)
    info["raw_tag_text"] = raw_text
    if _raw_text_has_chapter(raw_text):
        info["has_chapters"] = True


def _inspect_full_tags(filepath: Path, info: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        audio = mutagen_module.File(str(filepath))
        if audio is None:
            return
        _apply_technical_info(info, audio)
        tags = getattr(audio, "tags", None)
        if tags:
            _apply_full_tags(info, tags)


def _inspect_audio_file(filepath: Path) -> dict[str, Any]:
    info = _empty_audio_info()
    _inspect_easy_tags(filepath, info)
    _inspect_full_tags(filepath, info)
    return info


def _release_files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    files: list[Path] = []
    for root, _, names in os.walk(path):
        files.extend(Path(root) / name for name in names)
    return files


def _audio_files(files: list[Path]) -> list[Path]:
    allowed = SHARED_AUDIO_EXTENSIONS | AUDIOBOOK_CONTAINER_EXTENSIONS
    return [path for path in files if path.suffix.lower() in allowed]


def _specific_audiobook_result(files: list[Path]) -> AudioCategoryResult:
    extensions = sorted(
        {
            path.suffix.lower()
            for path in files
            if path.suffix.lower() in AUDIOBOOK_CONTAINER_EXTENSIONS
        }
    )
    return AudioCategoryResult(
        category="BOOK",
        is_audiobook=True,
        confidence=1.0,
        evidence=[
            f"audiobook-specific container format ({', '.join(extensions)})"
        ],
    )


def _ebook_result(has_audio: bool) -> AudioCategoryResult:
    if has_audio:
        return AudioCategoryResult(
            category="BOOK",
            is_audiobook=True,
            confidence=1.0,
            evidence=["Directory contains both eBook and audio files"],
        )
    return AudioCategoryResult(
        category="BOOK",
        is_audiobook=False,
        confidence=1.0,
        evidence=["Directory contains eBook file"],
    )


def _has_extension(files: list[Path], extensions: frozenset[str]) -> bool:
    return any(path.suffix.lower() in extensions for path in files)


def _book_early_result(
    files: list[Path], audio_files: list[Path]
) -> AudioCategoryResult | None:
    if _has_extension(files, AUDIOBOOK_CONTAINER_EXTENSIONS):
        return _specific_audiobook_result(files)
    if _has_extension(files, EBOOK_EXTENSIONS):
        return _ebook_result(bool(audio_files))
    return None


def _early_result(
    files: list[Path], audio_files: list[Path]
) -> AudioCategoryResult | None:
    if _has_extension(files, VIDEO_EXTENSIONS):
        return AudioCategoryResult(
            category="NONE", evidence=["Contains video files"]
        )
    book_result = _book_early_result(files, audio_files)
    if book_result is not None:
        return book_result
    if not audio_files:
        return AudioCategoryResult(category="NONE")
    return None


def _filename_counts(audio_files: list[Path]) -> tuple[int, int]:
    chapter_matches = 0
    music_matches = 0
    for path in audio_files:
        if AUDIOBOOK_FILENAME_REGEX.search(path.name):
            chapter_matches += 1
        elif MUSIC_FILENAME_REGEX.search(path.name):
            music_matches += 1
    return chapter_matches, music_matches


def _apply_filename_scores(
    scores: _ScoreCard, audio_files: list[Path]
) -> None:
    chapter_matches, music_matches = _filename_counts(audio_files)
    if chapter_matches > 0 and chapter_matches >= len(audio_files) * 0.3:
        scores.add_book(
            4.0,
            f"chapter/part filename pattern ({chapter_matches}/{len(audio_files)} files)",
        )
    if music_matches > 0 and music_matches >= len(audio_files) * 0.5:
        scores.add_music(
            2.0,
            f"standard numbered song titles ({music_matches}/{len(audio_files)} files)",
        )


def _audiobook_parent_hint(path: Path) -> bool:
    lowered = path.name.lower()
    return any(
        marker in lowered
        for marker in (
            "audiobook",
            "audiobooks",
            "audio book",
            "audio books",
            "readarr",
            "libby",
        )
    )


def _structured_music_name(path: Path) -> bool:
    release_name = path.name.replace("_", " ")
    artist_album = bool(re.search(r"\S\s+-\s+\S", release_name))
    music_marker = bool(
        re.search(
            r"(?:\b(?:16|24)BIT\b|\b(?:WEB|CD)[ ._-]*(?:FLAC|MP3|AAC)\b|\[(?:FLAC|MP3|AAC)\])",
            release_name,
            re.I,
        )
    )
    return artist_album and music_marker


def _is_lidarr_path(path: Path) -> bool:
    return any(part.casefold() == "lidarr" for part in path.parts)


def _apply_music_path_scores(scores: _ScoreCard, path: Path) -> None:
    if _is_lidarr_path(path):
        scores.add_music(6.0, "Lidarr library path")
    if _structured_music_name(path):
        scores.add_music(3.0, "structured artist/album music release name")
    if SCENE_MUSIC_RELEASE_REGEX.search(path.name):
        scores.add_music(6.0, "scene music release name")
    release_name = path.name.replace("_", " ")
    if DATED_MUSIC_RELEASE_REGEX.search(release_name):
        scores.add_music(6.0, "dated artist/title music release name")


def _apply_path_scores(scores: _ScoreCard, path: Path) -> None:
    if _audiobook_parent_hint(path):
        scores.add_book(2.0, f"parent directory hint ('{path.name}')")
    _apply_music_path_scores(scores, path)


def _summary_flag_values(parsed: dict[str, Any]) -> dict[str, bool]:
    return {
        "has_chapters": bool(parsed["has_chapters"]),
        "has_narrator": bool(parsed["narrator"]),
        "has_author": bool(parsed["author"]),
        "has_isbn_asin": any((parsed["isbn"], parsed["asin"])),
        "has_musicbrainz": bool(parsed["has_musicbrainz"]),
        "has_discogs": bool(parsed["has_discogs"]),
        "has_catalog_no": bool(parsed["has_catalog_no"]),
    }


def _set_summary_flags(
    summary: _SampleSummary, parsed: dict[str, Any]
) -> None:
    for field_name, enabled in _summary_flag_values(parsed).items():
        if enabled:
            setattr(summary, field_name, True)


def _positive_at_most(value: float, maximum: float) -> bool:
    return value > 0 and value <= maximum


def _increment_technical_counts(
    summary: _SampleSummary, parsed: dict[str, Any]
) -> None:
    if parsed["channels"] == 1:
        summary.mono_count += 1
    if _positive_at_most(parsed["bitrate"] // 1000, 128):
        summary.low_bitrate_count += 1
    if _positive_at_most(parsed["sample_rate"], 32000):
        summary.low_samplerate_count += 1
    if parsed["length"] >= 900:
        summary.long_track_count += 1


def _sample_summary(audio_files: list[Path]) -> tuple[_SampleSummary, int]:
    sample_files = audio_files[:30]
    summary = _SampleSummary()
    for path in sample_files:
        parsed = _inspect_audio_file(path)
        summary.genres.update(
            str(genre).lower() for genre in parsed["genres"] if genre
        )
        _set_summary_flags(summary, parsed)
        _increment_technical_counts(summary, parsed)
    return summary, len(sample_files)


def _matches_genre(genre: str, markers: frozenset[str]) -> bool:
    return any(marker in genre for marker in markers)


def _genre_kind(genre: str) -> str | None:
    normalized = re.sub(r"\s+", " ", genre).strip()
    if normalized in PODCAST_GENRES:
        return "podcast"
    if _matches_genre(genre, SPOKEN_GENRES):
        return "book"
    if _matches_genre(genre, MUSIC_GENRES):
        return "music"
    return None


def _apply_genre_score(scores: _ScoreCard, genres: set[str]) -> None:
    for genre in genres:
        kind = _genre_kind(genre)
        if kind == "podcast":
            scores.add_podcast(7.0, f"podcast metadata genre ('{genre}')")
            return
        if kind == "book":
            scores.add_book(5.0, f"spoken-word / audiobook genre ('{genre}')")
            return
        if kind == "music":
            scores.add_music(4.0, f"recognized music genre ('{genre}')")
            return


def _apply_book_metadata_scores(
    scores: _ScoreCard, summary: _SampleSummary
) -> None:
    signals = (
        (summary.has_chapters, 5.0, "embedded chapter metadata"),
        (summary.has_narrator, 4.0, "narrator metadata"),
        (summary.has_author, 3.0, "author metadata"),
        (summary.has_isbn_asin, 4.0, "ISBN or ASIN metadata"),
    )
    for enabled, value, evidence in signals:
        if enabled:
            scores.add_book(value, evidence)


def _apply_music_metadata_scores(
    scores: _ScoreCard, summary: _SampleSummary
) -> None:
    signals = (
        (summary.has_musicbrainz, 5.0, "MusicBrainz metadata tags"),
        (summary.has_discogs, 5.0, "Discogs metadata tags"),
        (
            summary.has_catalog_no,
            3.0,
            "music label / catalogue number metadata",
        ),
    )
    for enabled, value, evidence in signals:
        if enabled:
            scores.add_music(value, evidence)


def _apply_metadata_scores(
    scores: _ScoreCard, summary: _SampleSummary
) -> None:
    _apply_book_metadata_scores(scores, summary)
    _apply_music_metadata_scores(scores, summary)


def _majority(count: int, sample_count: int) -> bool:
    return count > 0 and count >= sample_count * 0.5


def _apply_technical_scores(
    scores: _ScoreCard, summary: _SampleSummary, sample_count: int
) -> None:
    if _majority(summary.mono_count, sample_count):
        scores.add_book(
            3.0, f"mono audio ({summary.mono_count}/{sample_count} files)"
        )
    if _majority(summary.low_bitrate_count, sample_count):
        scores.add_book(
            2.0,
            f"low bitrate audio ({summary.low_bitrate_count}/{sample_count} files)",
        )
    if _majority(summary.low_samplerate_count, sample_count):
        scores.add_book(
            2.0,
            f"low sample rate audio ({summary.low_samplerate_count}/{sample_count} files)",
        )
    if summary.long_track_count > 0:
        scores.add_book(
            3.0,
            f"long individual tracks (>15 min) ({summary.long_track_count} files)",
        )


def _podcast_result(scores: _ScoreCard) -> AudioCategoryResult | None:
    if scores.podcast < 4.0 or scores.podcast <= max(
        scores.book, scores.music
    ):
        return None
    return AudioCategoryResult(
        category="PODCAST",
        is_audiobook=False,
        confidence=min(1.0, scores.podcast / 10.0),
        evidence=scores.podcast_evidence,
    )


def _book_result(scores: _ScoreCard) -> AudioCategoryResult | None:
    if scores.book < 3.0 or scores.book <= scores.music:
        return None
    return AudioCategoryResult(
        category="BOOK",
        is_audiobook=True,
        confidence=min(1.0, scores.book / 10.0),
        evidence=scores.book_evidence,
    )


def _music_result(scores: _ScoreCard) -> AudioCategoryResult | None:
    if scores.music < 3.0 or scores.music <= scores.book:
        return None
    return AudioCategoryResult(
        category="MUSIC",
        is_audiobook=False,
        confidence=min(1.0, scores.music / 10.0),
        evidence=scores.music_evidence,
    )


def _classification(
    scores: _ScoreCard, audio_files: list[Path]
) -> AudioCategoryResult:
    for result in (
        _podcast_result(scores),
        _book_result(scores),
        _music_result(scores),
    ):
        if result is not None:
            return result
    return AudioCategoryResult(
        category="AMBIGUOUS",
        is_audiobook=False,
        confidence=0.0,
        evidence=[
            f"shared {audio_files[0].suffix.lower()} extension",
            "no audiobook-specific metadata",
            "no reliable music metadata",
        ],
    )


async def detect_audio_category(
    _meta: Any, path: Path | str
) -> AudioCategoryResult:
    """Classify audio as BOOK (audiobook), MUSIC, PODCAST, AMBIGUOUS, or NONE."""
    path_obj = Path(path)
    if not path_obj.exists():
        return AudioCategoryResult(category="NONE")
    files = _release_files(path_obj)
    audio_files = _audio_files(files)
    early = _early_result(files, audio_files)
    if early is not None:
        return early

    scores = _ScoreCard(len(audio_files))
    _apply_filename_scores(scores, audio_files)
    _apply_path_scores(scores, path_obj)
    summary, sample_count = _sample_summary(audio_files)
    _apply_genre_score(scores, summary.genres)
    _apply_metadata_scores(scores, summary)
    _apply_technical_scores(scores, summary, sample_count)
    return _classification(scores, audio_files)
