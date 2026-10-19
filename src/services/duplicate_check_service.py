# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
import unicodedata
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.UNIT3D.hawkeuno import HawkeUno


class DupeEntry(TypedDict, total=False):
    name: str
    size: int | str | None
    files: list[str]
    file_count: int
    trumpable: bool
    link: str | None
    download: str | None
    flags: list[str]
    id: int | str | None
    type: str | None
    res: str | None
    internal: int | bool
    bd_info: str | None
    description: str | None


type DupeInput = str | DupeEntry | MutableMapping[str, Any]


class AttributeCheck(TypedDict):
    key: str
    uuid_flag: bool
    condition: Callable[[str], bool]
    exclude_msg: Callable[[str], str]


@dataclass(frozen=True)
class _DupeFilterContext:
    meta: Meta
    tracker_name: str
    dupe_count: int
    has_repack_in_uuid: bool
    normalized_encoder: str
    video_encode_lower: str
    file_size: int | None
    has_is_disc: bool
    target_hdr: set[str]
    target_season: Any
    target_episode: Any
    target_resolution: str
    tag: str
    is_dvd: bool
    is_dvdrip: bool
    web_dl: bool
    target_source: str
    is_sd: int
    is_tv_pack: bool
    target_season_number: int | None
    filenames: list[str]
    filelist: list[str]
    is_exact_match_only: bool
    prefers_repack: bool
    preferred_upload_is_repack: bool
    release_group: str
    repack_pattern: re.Pattern[str]


@dataclass(frozen=True)
class _PreparedDupe:
    entry: DupeEntry
    name: str
    size: Any
    files: list[str]
    file_count: int
    normalized: str
    type_id: str | None
    res_id: str | None
    flags: list[str]
    file_hdr: set[str]


type ExclusionDecision = bool | None
type SyncExclusionHandler = Callable[
    [_DupeFilterContext, _PreparedDupe], ExclusionDecision
]
type AsyncExclusionHandler = Callable[
    [_DupeFilterContext, _PreparedDupe], Awaitable[ExclusionDecision]
]


class DupeChecker:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @staticmethod
    def game_platform_category(platform: str) -> str:
        value = platform.lower()
        nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
        groups = (
            (
                "playstation",
                (
                    "playstation",
                    "ps5",
                    "ps4",
                    "ps3",
                    "ps2",
                    "ps1",
                    "psp",
                    "vita",
                ),
            ),
            ("xbox", ("xbox",)),
            (nin_term, (nin_term, "switch", "wii", "3ds", "nds", "ds")),
        )
        for category, markers in groups:
            if any(marker in value for marker in markers):
                return category
        return "pc"

    @staticmethod
    def _strip_game_release_group(name: str) -> str:
        if "-" not in name:
            return name
        prefix, suffix = name.rsplit("-", 1)
        return (
            prefix
            if len(suffix.strip()) < 15 and " " not in suffix.strip()
            else name
        )

    @staticmethod
    def _remove_game_tokens(name: str, tokens: Sequence[str]) -> str:
        for token in tokens:
            name = re.sub(rf"\b{re.escape(token)}\b", "", name)
        return name

    @classmethod
    def clean_game_title(cls, name: str) -> str:
        value = cls._strip_game_release_group(name.lower())
        value = re.sub(
            r"(?i)\b(?:update|patch|build|version|ver|v)\b[.:=\-_\s]*\d+[\d._-]*",
            "",
            value,
        )
        value = re.sub(r"(?i)\bv\d+[\d._-]*\b", "", value)
        value = re.sub(r"\b\d+(?:\.\d+)+\b", "", value)
        value = re.sub(r"\b(?:19|20)\d{2}\b", "", value)
        nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
        value = cls._remove_game_tokens(
            value,
            (
                "pc",
                "windows",
                "win",
                "mac",
                "osx",
                "linux",
                "ps1",
                "ps2",
                "ps3",
                "ps4",
                "ps5",
                "playstation",
                "xbox",
                "x360",
                "xone",
                "xsx",
                "switch",
                "nsw",
                nin_term,
            ),
        )
        value = cls._remove_game_tokens(
            value,
            (
                "gog",
                "steam",
                "epic",
                "multi",
                "multilang",
                "repack",
                "iso",
                "zip",
                "rar",
                "setup",
                "download",
                "cracked",
                "crack",
            ),
        )
        value = re.sub(r"[._\[\]()\-:+]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _dupe_list_files(dupe: DupeInput) -> list[Any] | None:
        if not isinstance(dupe, dict):
            return None
        files = dupe.get("files")
        return cast(list[Any], files) if isinstance(files, list) else None

    @classmethod
    def _limited_debug_dupe(cls, dupe: DupeInput) -> dict[str, Any] | Any:
        redacted = Redaction.redact_private_info(dupe)
        original_files = cls._dupe_list_files(dupe)
        if original_files is None:
            return redacted
        limited = cast(dict[str, Any], redacted).copy()
        limited_files = [str(item) for item in original_files]
        if len(limited_files) <= 10:
            return limited
        limited["files"] = [
            *limited_files[:10],
            f"... and {len(original_files) - 10} more files",
        ]
        return limited

    @classmethod
    def _debug_pre_filtered(
        cls, dupes: Sequence[DupeInput], meta: Meta, tracker_name: str
    ) -> None:
        if not meta.debug:
            return
        logger.debug(f"[cyan]Pre-filtered dupes from {tracker_name}")
        if not dupes:
            logger.debug(dupes)
            return
        logger.debug([cls._limited_debug_dupe(dupe) for dupe in dupes])

    @staticmethod
    def _reset_filter_meta(meta: Meta) -> None:
        meta.trumpable_id = None
        meta.season_pack_exists = False
        meta.season_pack_id = None
        meta.season_pack_link = None
        meta.season_pack_name = ""

    @staticmethod
    def _base_dupe_entry(name: str) -> DupeEntry:
        return {
            "name": name,
            "size": None,
            "files": [],
            "file_count": 0,
            "trumpable": False,
            "link": None,
            "download": None,
            "flags": [],
            "id": None,
            "type": None,
            "res": None,
            "internal": 0,
            "bd_info": None,
            "description": None,
        }

    @staticmethod
    def _normalized_dupe_files(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(file) for file in cast(list[Any], value)]
        if isinstance(value, str) and value:
            return [value]
        return []

    @classmethod
    def _dupe_file_count(cls, value: dict[str, Any], files: list[str]) -> int:
        if "file_count" not in value:
            return len(files)
        return cls._coerce_int(value.get("file_count")) or 0

    @classmethod
    def _dict_dupe_entry(cls, value: dict[str, Any]) -> DupeEntry:
        entry = cls._base_dupe_entry(str(value.get("name", "")))
        files = cls._normalized_dupe_files(value.get("files"))
        entry.update(
            {
                "size": value.get("size"),
                "files": files,
                "file_count": cls._dupe_file_count(value, files),
                "trumpable": bool(value.get("trumpable", False)),
                "link": value.get("link"),
                "download": value.get("download"),
                "flags": cast(list[str], value.get("flags", [])),
                "id": value.get("id"),
                "type": value.get("type"),
                "res": value.get("res"),
                "internal": value.get("internal", 0),
                "bd_info": value.get("bd_info", ""),
                "description": value.get("description", ""),
            }
        )
        return entry

    @classmethod
    def _normalize_dupes(cls, dupes: Sequence[DupeInput]) -> list[DupeEntry]:
        processed: list[DupeEntry] = []
        for dupe in dupes:
            if isinstance(dupe, str):
                processed.append(cls._base_dupe_entry(dupe))
            elif isinstance(dupe, dict):
                processed.append(
                    cls._dict_dupe_entry(cast(dict[str, Any], dupe))
                )
        return processed

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except TypeError, ValueError:
            return None

    @staticmethod
    def _first_mediainfo_track(meta: Meta) -> Mapping[str, Any] | None:
        media = meta.mediainfo.get("media", {})
        if not isinstance(media, Mapping):
            return None
        tracks = cast(Mapping[str, Any], media).get("track", [])
        if not isinstance(tracks, list) or not tracks:
            return None
        first = cast(list[Any], tracks)[0]
        return (
            cast(Mapping[str, Any], first)
            if isinstance(first, Mapping)
            else None
        )

    @classmethod
    def _target_file_size(cls, meta: Meta) -> int | None:
        if meta.is_disc == "BDMV":
            return None
        first = cls._first_mediainfo_track(meta)
        return (
            cls._coerce_int(first.get("FileSize"))
            if first is not None
            else None
        )

    @staticmethod
    def _sequence_filelist(value: Any) -> list[str]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return []
        return [str(item) for item in cast(Sequence[Any], value)]

    @classmethod
    def _target_filelist(cls, meta: Meta) -> tuple[list[str], list[str]]:
        if meta.is_disc:
            return [], []
        filelist = cls._sequence_filelist(meta.filelist)
        return filelist, [Path(item).name for item in filelist]

    @staticmethod
    def _repack_pattern() -> re.Pattern[str]:
        return re.compile(r"(?<![a-z0-9])repack\d*(?![a-z0-9])", re.IGNORECASE)

    @staticmethod
    def _tracker_filter_policy(tracker_name: str) -> tuple[bool, bool]:
        from src.integrations.trackers.registry import tracker_class_map

        tracker_cls = tracker_class_map.get(tracker_name.upper())
        return (
            bool(getattr(tracker_cls, "exact_match_only", False)),
            bool(getattr(tracker_cls, "prefers_repack", False)),
        )

    @staticmethod
    def _upload_is_repack(
        meta: Meta, pattern: re.Pattern[str], prefers_repack: bool
    ) -> bool:
        if not prefers_repack:
            return False
        values = (meta.repack, meta.name, meta.uuid)
        return any(pattern.search(str(value or "")) for value in values)

    @staticmethod
    def _clear_repack_metadata(
        meta: Meta, tracker_name: str, enabled: bool
    ) -> None:
        if not enabled:
            return
        meta.pop(f"{tracker_name}_preferred_repack", None)
        meta.pop(f"{tracker_name}_repack_replaces", None)

    @staticmethod
    def _context_season_number(value: Any) -> int | None:
        match = re.search(r"[sS](\d+)", str(value or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _context_tag(meta: Meta) -> str:
        if not meta.tag:
            return ""
        return meta.tag.lower().replace("-", " ")

    @classmethod
    def _context_is_tv_pack(cls, meta: Meta) -> bool:
        if meta.category != "TV":
            return False
        return (cls._coerce_int(meta.tv_pack) or 0) == 1

    @staticmethod
    def _context_release_group(meta: Meta) -> str:
        return str(meta.tag or "").lstrip("-").strip().casefold()

    @classmethod
    async def _build_filter_context(
        cls, dupes: Sequence[DupeInput], meta: Meta, tracker_name: str
    ) -> _DupeFilterContext:
        pattern = cls._repack_pattern()
        video_encode = str(meta.video_encode or "")
        normalized_encoder = (
            await cls.normalize_filename(video_encode) if video_encode else ""
        )
        target_hdr = await cls.refine_hdr_terms(cast(str | None, meta.hdr))
        filelist, filenames = cls._target_filelist(meta)
        exact_only, prefers_repack = cls._tracker_filter_policy(tracker_name)
        preferred_upload_is_repack = cls._upload_is_repack(
            meta, pattern, prefers_repack
        )
        cls._clear_repack_metadata(meta, tracker_name, prefers_repack)
        return _DupeFilterContext(
            meta=meta,
            tracker_name=tracker_name,
            dupe_count=len(dupes),
            has_repack_in_uuid="repack" in meta.uuid.lower(),
            normalized_encoder=normalized_encoder,
            video_encode_lower=video_encode.lower(),
            file_size=cls._target_file_size(meta),
            has_is_disc=bool(meta.is_disc),
            target_hdr=target_hdr,
            target_season=meta.season,
            target_episode=meta.episode,
            target_resolution=str(meta.resolution or ""),
            tag=cls._context_tag(meta),
            is_dvd=meta.is_disc == "DVD",
            is_dvdrip=meta.type == "DVDRIP",
            web_dl=meta.type == "WEBDL",
            target_source=str(meta.source),
            is_sd=int(meta.sd or 0),
            is_tv_pack=cls._context_is_tv_pack(meta),
            target_season_number=cls._context_season_number(meta.season),
            filenames=filenames,
            filelist=filelist,
            is_exact_match_only=exact_only,
            prefers_repack=prefers_repack,
            preferred_upload_is_repack=preferred_upload_is_repack,
            release_group=cls._context_release_group(meta),
            repack_pattern=pattern,
        )

    @staticmethod
    def _split_single_file_value(values: list[str]) -> list[str]:
        if len(values) != 1:
            return values
        value = values[0]
        return (
            [item.strip() for item in value.split(",")]
            if "," in value
            else values
        )

    @classmethod
    def _prepared_files(cls, entry: DupeEntry) -> list[str]:
        values = [
            str(file) for file in cast(list[Any], entry.get("files") or [])
        ]
        return cls._split_single_file_value(values)

    @classmethod
    async def _prepared_hdr(
        cls, flags: list[str], normalized: str
    ) -> set[str]:
        if not flags:
            return await cls.refine_hdr_terms(normalized)
        hdr: set[str] = set()
        for flag in flags:
            upper = flag.upper()
            if upper == "DV":
                hdr.add("DV")
            elif upper in {"HDR", "HDR10", "HDR10+"}:
                hdr.add("HDR")
        return hdr

    @classmethod
    async def _prepare_dupe(cls, entry: DupeEntry) -> _PreparedDupe:
        name = str(entry.get("name", ""))
        files = cls._prepared_files(entry)
        normalized = await cls.normalize_filename(name)
        flags = [
            str(flag) for flag in cast(list[Any], entry.get("flags") or [])
        ]
        return _PreparedDupe(
            entry=entry,
            name=name,
            size=entry.get("size"),
            files=files,
            file_count=cls._coerce_int(entry.get("file_count", 0)) or 0,
            normalized=normalized,
            type_id=cast(str | None, entry.get("type")),
            res_id=cast(str | None, entry.get("res")),
            flags=flags,
            file_hdr=await cls._prepared_hdr(flags, normalized),
        )

    @staticmethod
    def _remember_match(
        ctx: _DupeFilterContext, state: _PreparedDupe, reason: str
    ) -> None:
        meta = ctx.meta
        entry = state.entry
        prefix = ctx.tracker_name
        meta[f"{prefix}_matched_name"] = entry.get("name")
        if entry.get("link"):
            meta[f"{prefix}_matched_link"] = entry.get("link")
        if entry.get("download"):
            meta[f"{prefix}_matched_download"] = entry.get("download")
        meta[f"{prefix}_matched_reason"] = reason
        if state.file_count:
            meta[f"{prefix}_matched_file_count"] = state.file_count
        if entry.get("id"):
            meta[f"{prefix}_matched_id"] = entry.get("id")

    @staticmethod
    def _log_exclusion(
        ctx: _DupeFilterContext, reason: str, item: str
    ) -> None:
        if ctx.meta.debug:
            logger.debug(f"[yellow]Excluding result due to {reason}: {item}")

    @staticmethod
    def _debug_prepared_dupe(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> None:
        if not ctx.meta.debug:
            return
        logger.debug(f"[debug] Evaluating dupe: {state.name}")
        logger.debug(f"[debug] Normalized dupe: {state.normalized}")
        logger.debug(f"[debug] Target resolution: {ctx.target_resolution}")
        logger.debug(f"[debug] Target source: {ctx.target_source}")
        logger.debug(f"[debug] File HDR terms: {state.file_hdr}")
        logger.debug(f"[debug] Flags: {state.flags}")
        logger.debug(f"[debug] Target HDR terms: {ctx.target_hdr}")
        logger.debug(f"[debug] Target Season: {ctx.target_season}")
        logger.debug(f"[debug] Target Episode: {ctx.target_episode}")
        logger.debug(f"[debug] TAG: {ctx.tag}")
        logger.debug("[debug] Evaluating repack condition:")
        logger.debug(f"  has_repack_in_uuid: {ctx.has_repack_in_uuid}")
        logger.debug(
            f"  'repack' in each.lower(): {'repack' in state.name.lower()}"
        )
        logger.debug(f"[debug] meta.uuid: {ctx.meta.uuid}")
        logger.debug(f"[debug] normalized encoder: {ctx.normalized_encoder}")
        logger.debug(
            f"[debug] type_id: {state.type_id}, res_id: {state.res_id}"
        )
        logger.debug(f"[debug] link: {state.entry.get('link')}")
        logger.debug(
            f"[debug] files: {state.files[:10]}{'...' if len(state.files) > 10 else ''}"
        )
        logger.debug(f"[debug] file_count: {state.file_count}")

    @staticmethod
    def _game_titles_match(target: str, candidate: str) -> bool:
        if target == candidate:
            return True
        if not target or not candidate:
            return False
        if re.search(rf"\b{re.escape(target)}\b", candidate):
            return True
        return bool(re.search(rf"\b{re.escape(candidate)}\b", target))

    @staticmethod
    def _game_target_title(meta: Meta) -> str:
        if meta.title:
            return str(meta.title)
        if meta.name:
            return str(meta.name)
        return ""

    @classmethod
    def _game_platform_mismatch_reason(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> str:
        target = cls.game_platform_category(str(ctx.meta.platform or ""))
        candidate = cls.game_platform_category(
            str(state.entry.get("type", ""))
        )
        if target == candidate:
            return ""
        return f"game platform mismatch (expected {target}, got {candidate})"

    @classmethod
    def _game_title_mismatch(
        cls, target_title: str, state: _PreparedDupe
    ) -> bool:
        clean_target = cls.clean_game_title(target_title)
        clean_candidate = cls.clean_game_title(state.name)
        logger.debug(
            f"[debug] Game title comparison: Target='{clean_target}' vs Dupe='{clean_candidate}'"
        )
        return not cls._game_titles_match(clean_target, clean_candidate)

    @classmethod
    async def _game_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if ctx.meta.category != "GAME":
            return None
        target_title = cls._game_target_title(ctx.meta)
        if not target_title.strip():
            cls._log_exclusion(ctx, "empty target game title", state.name)
            return True
        platform_reason = cls._game_platform_mismatch_reason(ctx, state)
        if platform_reason:
            cls._log_exclusion(ctx, platform_reason, state.name)
            return True
        if cls._game_title_mismatch(target_title, state):
            cls._log_exclusion(ctx, "game title mismatch", state.name)
            return True
        cls._remember_match(ctx, state, "title")
        logger.debug(f"[cyan]Game duplicate matched: {state.name}")
        return False

    @staticmethod
    def _clean_book_title(value: str) -> str:
        normalized = (
            unicodedata.normalize("NFKD", value)
            .encode("ascii", "ignore")
            .decode("utf-8")
            .lower()
        )
        normalized = re.sub(
            r"\.(pdf|epub|mobi|azw3|kfx|cbz|cbr|mp3|m4b|flac|aac|m4a|ogg|wav)$",
            "",
            normalized,
        )
        normalized = re.sub(r"[._\[\]()]", " ", normalized)
        normalized = re.sub(r"(?<!\s)-(?!\s)", " ", normalized)
        return re.sub(r"[^a-z0-9\s\-:]", "", normalized).strip()

    @staticmethod
    def _book_title_candidates(cleaned: str) -> list[str]:
        candidates: list[str] = []
        for part in re.split(r"[:]|\s+-\s+|\s+by\s+", cleaned):
            value = re.sub(r"[^a-z0-9\s]", "", part)
            value = re.sub(r"\s+", " ", value).strip()
            if len(value) >= 2:
                candidates.append(value)
        return candidates

    @staticmethod
    def _normalize_book_candidate(candidate: str) -> str:
        without_suffix = re.sub(r"\b(?:19|20)\d{2}\b.*$", "", candidate)
        return re.sub(r"\s+", " ", without_suffix).strip()

    @classmethod
    def _book_main_candidate(cls, candidates: list[str], author: str) -> str:
        selected = next(
            (candidate for candidate in candidates if candidate != author),
            candidates[0],
        )
        return cls._normalize_book_candidate(selected)

    @classmethod
    def _book_titles_match(
        cls, target: str, candidate: str, author: Any
    ) -> bool:
        norm_target = re.sub(
            r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", target)
        ).strip()
        norm_candidate = re.sub(
            r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", candidate)
        ).strip()
        if norm_target == norm_candidate:
            return True
        target_candidates = cls._book_title_candidates(target)
        candidate_candidates = cls._book_title_candidates(candidate)
        if not target_candidates or not candidate_candidates:
            return False
        clean_author = cls._clean_book_title(str(author or ""))
        return cls._book_main_candidate(
            target_candidates, clean_author
        ) == cls._book_main_candidate(candidate_candidates, clean_author)

    @staticmethod
    def _book_compatible_extensions(
        meta: Meta, target_format: str
    ) -> set[str]:
        extensions: set[str] = {target_format} if target_format else set()
        if meta.audiobook or target_format == "audiobook":
            extensions.update(
                {
                    "mp3",
                    "flac",
                    "m4b",
                    "m4a",
                    "wav",
                    "ogg",
                    "aac",
                    "ac3",
                    "wma",
                    "opus",
                }
            )
        elif target_format in {"book", "ebook"}:
            extensions.update(
                {"pdf", "epub", "mobi", "azw3", "kfx", "cbz", "cbr"}
            )
        return extensions

    @staticmethod
    def _payload_has_format(
        paths: Sequence[str], extensions: set[str]
    ) -> bool:
        if not extensions:
            return False
        return any(
            Path(path).suffix.casefold().lstrip(".") in extensions
            for path in paths
        )

    @staticmethod
    def _audiobook_types() -> set[str]:
        return {
            "audiobook",
            "mp3",
            "flac",
            "m4b",
            "m4a",
            "wav",
            "ogg",
            "aac",
            "ac3",
            "wma",
            "opus",
        }

    @staticmethod
    def _book_name_has_audio_marker(name_lower: str) -> bool:
        return "audiobook" in name_lower or "audio book" in name_lower

    @classmethod
    def _book_dupe_is_audiobook(cls, state: _PreparedDupe) -> bool:
        audiobook_types = cls._audiobook_types()
        dupe_type = str(state.entry.get("type") or "").lower()
        if dupe_type in audiobook_types:
            return True
        name_lower = state.name.lower()
        if cls._book_name_has_audio_marker(name_lower):
            return True
        return any(
            re.search(rf"\b{re.escape(value)}\b", name_lower)
            for value in audiobook_types
        )

    @staticmethod
    def _ebook_files_match_format(files: list[str], target_type: str) -> bool:
        suffix = f".{target_type}"
        return any(file.lower().endswith(suffix) for file in files)

    @staticmethod
    def _ebook_name_matches_format(name: str, target_type: str) -> bool:
        name_lower = name.lower()
        if name_lower.endswith(f".{target_type}"):
            return True
        return bool(re.search(rf"\b{re.escape(target_type)}\b", name_lower))

    @classmethod
    def _ebook_format_matches(
        cls, state: _PreparedDupe, target_type: str
    ) -> bool:
        dupe_type = str(state.entry.get("type") or "").lower()
        if target_type == dupe_type:
            return True
        if cls._ebook_files_match_format(state.files, target_type):
            return True
        return cls._ebook_name_matches_format(state.name, target_type)

    @staticmethod
    def _filename_candidate_matches(
        local_file: str, candidate: str, partial: bool
    ) -> bool:
        local = local_file.lower()
        other = candidate.lower()
        return local in other if partial else local == other

    @classmethod
    def _matching_local_filename(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe, partial: bool
    ) -> str | None:
        for local_file in ctx.filenames:
            if any(
                cls._filename_candidate_matches(local_file, candidate, partial)
                for candidate in state.files
            ):
                return local_file
        return None

    @classmethod
    def _record_file_count_match(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if not state.file_count or state.file_count != len(ctx.filelist):
            return False
        ctx.meta.file_count_match = state.file_count
        cls._remember_match(ctx, state, "file_count")
        return True

    @classmethod
    def _record_filename_match(
        cls,
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        *,
        partial: bool = False,
    ) -> bool:
        if cls._matching_local_filename(ctx, state, partial) is None:
            return False
        ctx.meta.filename_match = (
            f"{state.entry.get('name')} = {state.entry.get('link')}"
        )
        cls._remember_match(ctx, state, "filename")
        cls._remember_match(ctx, state, "id")
        cls._record_file_count_match(ctx, state)
        return True

    @classmethod
    async def _book_exact_payload_decision(
        cls,
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        exact_payload: bool,
        target_format: str,
    ) -> ExclusionDecision:
        extensions = cls._book_compatible_extensions(ctx.meta, target_format)
        if not exact_payload or not cls._payload_has_format(
            [*ctx.filenames, *state.files], extensions
        ):
            return None
        cls._record_filename_match(ctx, state)
        cls._remember_match(ctx, state, "exact_payload")
        logger.debug(
            f"[cyan]Exact book payload duplicate matched: {state.name}"
        )
        return False

    @classmethod
    def _book_format_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe, target_format: str
    ) -> ExclusionDecision:
        target_is_audiobook = bool(ctx.meta.audiobook)
        if target_is_audiobook != cls._book_dupe_is_audiobook(state):
            cls._log_exclusion(
                ctx,
                "book format type mismatch (audiobook vs ebook)",
                state.name,
            )
            return True
        if target_is_audiobook or cls._ebook_format_matches(
            state, target_format
        ):
            return None
        if ctx.tracker_name == "CAPYBARABR":
            logger.debug(
                "[debug] CAPYBARABR allows only one ebook format per book, so different formats are considered duplicates."
            )
            return None
        cls._log_exclusion(
            ctx,
            f"book format type mismatch (expected {target_format})",
            state.name,
        )
        return True

    @staticmethod
    def _book_target_title(meta: Meta) -> str:
        if meta.title:
            return str(meta.title)
        if meta.name:
            return str(meta.name)
        return ""

    @classmethod
    def _book_title_format_decision(
        cls,
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        target_title: str,
        target_format: str,
    ) -> ExclusionDecision:
        clean_target = cls._clean_book_title(target_title)
        clean_candidate = cls._clean_book_title(state.name)
        if not cls._book_titles_match(
            clean_target, clean_candidate, ctx.meta.author
        ):
            cls._log_exclusion(ctx, "book title mismatch", state.name)
            return True
        return cls._book_format_decision(ctx, state, target_format)

    @classmethod
    def _finalize_book_match(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe, exact_payload: bool
    ) -> bool:
        if ctx.filenames and state.files:
            cls._record_filename_match(ctx, state)
        if exact_payload:
            cls._remember_match(ctx, state, "exact_payload")
            logger.debug(
                f"[cyan]Exact book payload duplicate matched: {state.name}"
            )
            return False
        cls._remember_match(ctx, state, "title")
        logger.debug(f"[cyan]Book duplicate matched: {state.name}")
        return False

    @classmethod
    async def _active_book_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        target_title = cls._book_target_title(ctx.meta)
        if not target_title.strip():
            cls._log_exclusion(ctx, "empty target book title", state.name)
            return True
        exact_payload = await cls.is_exact_match(state.entry, ctx.meta)
        target_format = str(ctx.meta.type or "").casefold().lstrip(".")
        exact_decision = await cls._book_exact_payload_decision(
            ctx, state, exact_payload, target_format
        )
        if exact_decision is not None:
            return exact_decision
        semantic_decision = cls._book_title_format_decision(
            ctx, state, target_title, target_format
        )
        if semantic_decision is not None:
            return semantic_decision
        return cls._finalize_book_match(ctx, state, exact_payload)

    @classmethod
    async def _book_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if ctx.meta.category != "BOOK":
            return None
        return await cls._active_book_decision(ctx, state)

    @staticmethod
    def _target_episode_numbers(ctx: _DupeFilterContext) -> set[int]:
        return {
            int(value) for value in re.findall(r"\d+", str(ctx.target_episode))
        }

    @staticmethod
    def _pack_episode_numbers(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> set[int]:
        if ctx.target_season_number is None:
            return set()
        pattern = re.compile(
            rf"(?i)(?<!\w)S0*{ctx.target_season_number}E(\d+)"
        )
        values: set[int] = set()
        for file_name in state.files:
            values.update(int(value) for value in pattern.findall(file_name))
        return values

    @classmethod
    def _season_pack_contains_target(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool | None:
        target_numbers = cls._target_episode_numbers(ctx)
        if (
            not state.files
            or ctx.target_season_number is None
            or not target_numbers
        ):
            return None
        return target_numbers.issubset(cls._pack_episode_numbers(ctx, state))

    @classmethod
    def _record_season_pack(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> None:
        meta = ctx.meta
        meta.season_pack_exists = True
        meta.season_pack_name = state.name
        meta.season_pack_link = state.entry.get("link")
        meta.season_pack_id = state.entry.get("id")
        logger.debug(
            f"[yellow]Season pack detected for episode upload: {state.name}"
        )
        logger.debug(
            f"[yellow]Your episode {ctx.target_season}{ctx.target_episode} is contained in existing season pack"
        )
        cls._remember_match(ctx, state, "season_pack_contains_episode")

    @staticmethod
    def _tv_episode_filter_enabled(ctx: _DupeFilterContext) -> bool:
        return ctx.meta.category == "TV" and bool(ctx.target_episode)

    @classmethod
    def _season_pack_decision(
        cls,
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        matches: bool,
        is_season: bool,
    ) -> ExclusionDecision:
        if not matches or not is_season:
            return None
        contains = cls._season_pack_contains_target(ctx, state)
        if contains is False:
            cls._log_exclusion(
                ctx,
                f"season pack does not contain episode {ctx.target_episode}",
                state.name,
            )
            return True
        if contains is True:
            cls._record_season_pack(ctx, state)
            return False
        return None

    @staticmethod
    def _luminarr_episode_match(
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        matches: bool,
        is_season: bool,
    ) -> bool:
        if ctx.tracker_name != "LUMINARR" or not matches or is_season:
            return False
        if not ctx.target_resolution:
            return True
        return ctx.target_resolution.casefold() in state.normalized.casefold()

    @classmethod
    async def _tv_initial_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._tv_episode_filter_enabled(ctx):
            return None
        matches, is_season = await cls.is_season_episode_match(
            state.normalized, ctx.target_season, ctx.target_episode
        )
        pack_decision = cls._season_pack_decision(
            ctx, state, matches, is_season
        )
        if pack_decision is not None:
            return pack_decision
        if cls._luminarr_episode_match(ctx, state, matches, is_season):
            cls._remember_match(ctx, state, "luminarr_same_episode_resolution")
            return False
        return None

    @staticmethod
    def _tv_trump_source_matches(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        source = ctx.target_source.lower()
        type_id = str(state.type_id or "").lower()
        return bool(
            type_id
            and state.res_id
            and source in type_id
            and ctx.target_resolution == state.res_id
        )

    @staticmethod
    def _aither_settings(
        config: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        trackers = config.get("TRACKERS", {})
        if not isinstance(trackers, Mapping):
            return None
        aither = cast(Mapping[str, Any], trackers).get("AITHER", {})
        return (
            cast(Mapping[str, Any], aither)
            if isinstance(aither, Mapping)
            else None
        )

    @staticmethod
    def _normalized_group_values(groups: Any) -> set[str]:
        if not isinstance(groups, list):
            return set()
        return {
            str(group).strip().lstrip("-").casefold()
            for group in cast(list[Any], groups)
        }

    @classmethod
    def _normalized_internal_groups(
        cls, config: Mapping[str, Any]
    ) -> set[str]:
        aither = cls._aither_settings(config)
        if aither is None or aither.get("internal") is not True:
            return set()
        return cls._normalized_group_values(aither.get("internal_groups", []))

    def _internal_episode_allowed_with_config(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if state.entry.get("internal", 0) != 1:
            return True
        tag = ctx.tag.strip().lstrip("-").casefold()
        groups = self._normalized_internal_groups(self.config)
        return bool(
            tag and tag in groups and tag in state.normalized.casefold()
        )

    @staticmethod
    def _matched_episode_entries(
        ctx: _DupeFilterContext,
    ) -> list[dict[str, Any]]:
        value = ctx.meta.setdefault(
            f"{ctx.tracker_name}_matched_episode_ids", []
        )
        return cast(list[dict[str, Any]], value)

    @staticmethod
    def _episode_id_exists(
        entries: list[dict[str, Any]], entry_id: Any
    ) -> bool:
        if not entry_id:
            return False
        return any(existing.get("id") == entry_id for existing in entries)

    @staticmethod
    def _episode_link_exists(
        entries: list[dict[str, Any]], entry_link: Any, tracker_name: str
    ) -> bool:
        if not entry_link:
            return False
        return any(
            existing.get("link") == entry_link
            and existing.get("tracker") == tracker_name
            for existing in entries
        )

    @classmethod
    def _episode_entry_exists(
        cls,
        entries: list[dict[str, Any]],
        entry_id: Any,
        entry_link: Any,
        tracker_name: str,
    ) -> bool:
        return cls._episode_id_exists(
            entries, entry_id
        ) or cls._episode_link_exists(entries, entry_link, tracker_name)

    @classmethod
    def _record_matched_episode(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        entries = cls._matched_episode_entries(ctx)
        entry_id = state.entry.get("id")
        entry_link = state.entry.get("link")
        exists = cls._episode_entry_exists(
            entries, entry_id, entry_link, ctx.tracker_name
        )
        if entry_id and not exists:
            entries.append(
                {
                    "id": entry_id,
                    "name": state.name,
                    "link": entry_link,
                    "tracker": ctx.tracker_name,
                    "internal": state.entry.get("internal", 0),
                }
            )
            logger.debug(
                f"[debug] Added episode ID {entry_id} to matched list"
            )
            cls._remember_match(ctx, state, "season_pack_contains_episode")
            return False
        if exists and ctx.meta.debug:
            logger.debug(
                f"[debug] Skipping duplicate entry for episode ID {entry_id}"
            )
        return None

    @staticmethod
    def _tv_trump_enabled(ctx: _DupeFilterContext, is_season: bool) -> bool:
        return is_season and ctx.tracker_name in {"AITHER", "LST"}

    def _internal_trump_decision(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if self._internal_episode_allowed_with_config(ctx, state):
            return None
        if ctx.meta.debug:
            logger.debug(
                "[debug] Skipping internal episode for trumping since you're not the internal uploader."
            )
        self._log_exclusion(
            ctx,
            "internal episode belongs to a different internal group",
            state.name,
        )
        return True

    def _active_tv_trump_decision(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        logger.debug(
            f"[debug] Checking trumping: target_source='{ctx.target_source.lower()}', type_id='{str(state.type_id or '').lower()}', target_res='{ctx.target_resolution}', res_id='{state.res_id or ''}'"
        )
        internal_decision = self._internal_trump_decision(ctx, state)
        if internal_decision is not None:
            return internal_decision
        return self._record_matched_episode(ctx, state)

    async def _tv_trump_decision(
        self,
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        is_season: bool,
    ) -> ExclusionDecision:
        if not self._tv_trump_enabled(ctx, is_season):
            return None
        if not self._tv_trump_source_matches(ctx, state):
            return None
        return self._active_tv_trump_decision(ctx, state)

    async def _tv_final_decision(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if ctx.meta.category != "TV":
            return None
        matches, is_season = await self.is_season_episode_match(
            state.normalized, ctx.target_season, ctx.target_episode
        )
        logger.debug(f"[debug] Season/Episode match result: {matches}")
        logger.debug(f"[debug] is_season: {is_season}")
        trump_decision = await self._tv_trump_decision(ctx, state, is_season)
        if trump_decision is not None:
            return trump_decision
        if not matches:
            self._log_exclusion(ctx, "season/episode mismatch", state.name)
            return True
        return None

    @staticmethod
    def _same_release_name(ctx: _DupeFilterContext, entry: DupeEntry) -> bool:
        candidate = str(entry.get("name", "")).strip().casefold()
        target = str(ctx.meta.name or "").strip().casefold()
        return candidate == target

    async def _exact_only_decision(
        self, ctx: _DupeFilterContext, entry: DupeEntry
    ) -> ExclusionDecision:
        if not ctx.is_exact_match_only:
            return None
        if await self.is_exact_match(entry, ctx.meta):
            return False
        self._log_exclusion(
            ctx,
            "non-exact release (allowed on exact-match-only tracker)",
            str(entry.get("name", "")),
        )
        return True

    def _configured_dupe_tolerance(self, meta: Meta) -> float | None:
        value = meta.dupe_size_difference_tolerance
        if value is None:
            defaults = self.config.get("DEFAULT", {})
            if isinstance(defaults, Mapping):
                value = cast(Mapping[str, Any], defaults).get(
                    "dupe_size_difference_tolerance"
                )
        if value is None:
            return None
        try:
            return float(value)
        except TypeError, ValueError:
            return None

    @staticmethod
    def _dupe_size_difference_pct(
        meta: Meta, entry: DupeEntry
    ) -> float | None:
        from src.services.upload_decision_service import parse_size_to_bytes

        upload_size = meta.source_size
        if not upload_size or upload_size <= 0:
            return None
        dupe_size = parse_size_to_bytes(entry.get("size"))
        if not dupe_size or dupe_size <= 0:
            return None
        return abs(dupe_size - upload_size) / upload_size * 100

    def _size_tolerance_decision(
        self, ctx: _DupeFilterContext, entry: DupeEntry
    ) -> ExclusionDecision:
        tolerance = self._configured_dupe_tolerance(ctx.meta)
        if tolerance is None:
            return None
        try:
            difference = self._dupe_size_difference_pct(ctx.meta, entry)
        except Exception as error:
            logger.debug(
                f"[debug] Error in dupe size tolerance check: {error}"
            )
            return None
        if difference is None or difference < tolerance:
            return None
        self._log_exclusion(
            ctx,
            f"size difference ({difference:.2f}%) exceeding tolerance ({tolerance}%)",
            str(entry.get("name", "")),
        )
        return True

    async def _entry_preflight_decision(
        self, ctx: _DupeFilterContext, entry: DupeEntry
    ) -> ExclusionDecision:
        if self._same_release_name(ctx, entry):
            return False
        exact = await self._exact_only_decision(ctx, entry)
        if exact is not None:
            return exact
        return self._size_tolerance_decision(ctx, entry)

    @classmethod
    def _trumpable_flag_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        eligible = (
            ctx.tracker_name in {"AITHER", "LST"}
            and bool(state.entry.get("trumpable", False))
            and bool(state.res_id)
            and ctx.target_resolution == state.res_id
        )
        if not eligible:
            return None
        ctx.meta.trumpable_id = state.entry.get("id")
        cls._remember_match(ctx, state, "trumpable_id")
        return False

    @staticmethod
    def _disc_has_too_few_files(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        return bool(
            ctx.meta.is_disc and state.file_count and state.file_count < 2
        )

    @staticmethod
    def _disc_is_m2ts(ctx: _DupeFilterContext, state: _PreparedDupe) -> bool:
        return ctx.has_is_disc and state.name.lower().endswith(".m2ts")

    @staticmethod
    def _disc_has_file_extension(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if not ctx.has_is_disc:
            return False
        return bool(re.search(r"\.\w{2,4}$", state.name))

    @classmethod
    def _disc_file_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if cls._disc_has_too_few_files(ctx, state):
            cls._log_exclusion(
                ctx, "file count less than 2 for disc upload", state.name
            )
            return True
        if cls._disc_is_m2ts(ctx, state):
            return False
        if cls._disc_has_file_extension(ctx, state):
            cls._log_exclusion(
                ctx, "file extension mismatch (is_disc=True)", state.name
            )
            return True
        return None

    @staticmethod
    def _exclude_nonrepack_for_repack_upload(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        checks = (
            not ctx.prefers_repack,
            ctx.has_repack_in_uuid,
            "repack" not in state.normalized,
            bool(ctx.meta.tag),
            str(ctx.meta.tag or "").lower() in state.normalized,
        )
        return all(checks)

    @classmethod
    def _repack_filter_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._exclude_nonrepack_for_repack_upload(ctx, state):
            return None
        cls._log_exclusion(ctx, "repack release", state.name)
        return True

    @staticmethod
    def _sizes_match(entry_size: int | None, source_size: int | None) -> bool:
        if entry_size is None or source_size is None:
            return False
        return entry_size == source_size

    @staticmethod
    def _log_size_parse_failure(
        ctx: _DupeFilterContext, state: _PreparedDupe, entry_size: int | None
    ) -> None:
        if (
            not ctx.meta.debug
            or entry_size is not None
            or ctx.meta.source_size is None
        ):
            return
        logger.debug(
            f"[debug] Size comparison failed due to ValueError: entry_size={state.entry.get('size')}, source_size={ctx.meta.source_size}"
        )

    @classmethod
    def _mark_size_match(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        entry_size = cls._coerce_int(state.entry.get("size"))
        source_size = cls._coerce_int(ctx.meta.source_size)
        if cls._sizes_match(entry_size, source_size):
            ctx.meta.size_match = (
                f"{state.entry.get('name')} = {state.entry.get('link')}"
            )
            cls._remember_match(ctx, state, "size")
            return False
        cls._log_size_parse_failure(ctx, state, entry_size)
        return None

    @classmethod
    def _disc_size_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not ctx.meta.is_disc:
            return None
        return cls._mark_size_match(ctx, state)

    @classmethod
    def _record_partial_filename_match(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if cls._matching_local_filename(ctx, state, True) is None:
            return False
        ctx.meta.filename_match = (
            f"{state.entry.get('name')} = {state.entry.get('link')}"
        )
        cls._remember_match(ctx, state, "filename")
        return cls._record_file_count_match(ctx, state)

    @classmethod
    def _source_payload_cross_seed_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        eligible = not ctx.meta.is_disc and ctx.tracker_name in {
            "ALPHARATIO",
            "RETROFLIX",
        }
        if not eligible:
            return None
        if cls._record_partial_filename_match(ctx, state):
            return False
        return cls._mark_size_match(ctx, state)

    @classmethod
    def _beyondhd_size_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if ctx.meta.is_disc or ctx.tracker_name != "BEYONDHD":
            return None
        return cls._mark_size_match(ctx, state)

    @staticmethod
    def _set_filename_match(meta: Meta, state: _PreparedDupe) -> None:
        meta.filename_match = (
            f"{state.entry.get('name')} = {state.entry.get('link')}"
        )

    @classmethod
    def _beyondhd_name_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if ctx.tracker_name != "BEYONDHD":
            return None
        target_name = str(ctx.meta.name or "").replace("DD+", "DDP")
        if str(state.entry.get("name")) != target_name:
            return None
        cls._set_filename_match(ctx.meta, state)
        return False

    async def _hawkeuno_name_decision(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if ctx.tracker_name != "HAWKEUNO":
            return None
        result: Any = await HawkeUno(config=self.config).get_name(ctx.meta)
        name = (
            str(cast(dict[str, Any], result).get("name", result))
            if isinstance(result, dict)
            else str(result)
        )
        if str(state.entry.get("name")) != name:
            return None
        self._set_filename_match(ctx.meta, state)
        return False

    @staticmethod
    def _framestor_special_match(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if ctx.tracker_name not in {"BEYONDHD", "RETROFLIX", "ALPHARATIO"}:
            return False
        if "2160p" not in ctx.target_resolution or "2160p" not in state.name:
            return False
        return (
            "framestor" in state.name.lower()
            or "framestor" in ctx.meta.uuid.lower()
        )

    @staticmethod
    def _sd_special_match(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if ctx.is_sd != 1 or ctx.tracker_name not in {"BEYONDHD", "AITHER"}:
            return False
        if ctx.has_is_disc:
            return False
        return any(
            str(resolution) in state.name for resolution in (1080, 720, 2160)
        )

    @staticmethod
    def _hdr_1080_keep(ctx: _DupeFilterContext, state: _PreparedDupe) -> bool:
        return all(
            (
                bool(ctx.target_hdr),
                "1080p" in ctx.target_resolution,
                "2160p" in state.name,
            )
        )

    @classmethod
    def _special_keep_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if cls._framestor_special_match(ctx, state) or cls._sd_special_match(
            ctx, state
        ):
            return False
        if cls._hdr_1080_keep(ctx, state):
            cls._log_exclusion(ctx, "No 1080p HDR when 4K exists", state.name)
            return False
        return None

    @staticmethod
    def _aither_dvd_enabled(ctx: _DupeFilterContext) -> bool:
        return ctx.tracker_name in {"AITHER", "LST"} and ctx.is_dvd

    @classmethod
    def _aither_dvd_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._aither_dvd_enabled(ctx):
            return None
        tag = ctx.tag.strip()
        if not tag:
            return False
        return tag not in state.normalized

    @staticmethod
    def _has_web_term(value: str) -> bool:
        return any(
            term in value for term in ("web-dl", "web -dl", "webdl", "web dl")
        )

    @staticmethod
    def _has_bluray_term(value: str) -> bool:
        return any(
            term in value
            for term in ("blu-ray", "blu ray", "bluray", "blu -ray")
        )

    @staticmethod
    def _web_vs_hdtv_mismatch(
        ctx: _DupeFilterContext, state: _PreparedDupe, has_web: bool
    ) -> bool:
        return all((ctx.web_dl, "hdtv" in state.normalized, not has_web))

    @classmethod
    def _web_vs_bluray_mismatch(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe, has_web: bool
    ) -> bool:
        return all(
            (ctx.web_dl, cls._has_bluray_term(state.normalized), not has_web)
        )

    @staticmethod
    def _nonweb_vs_web_mismatch(
        ctx: _DupeFilterContext, has_web: bool
    ) -> bool:
        return all((not ctx.web_dl, has_web))

    @classmethod
    def _source_mismatch_reason(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> str:
        has_web = cls._has_web_term(state.normalized)
        if cls._web_vs_hdtv_mismatch(ctx, state, has_web):
            return "source mismatch: WEB-DL vs HDTV"
        if cls._web_vs_bluray_mismatch(ctx, state, has_web):
            return "source mismatch: WEB-DL vs BluRay"
        if cls._nonweb_vs_web_mismatch(ctx, has_web):
            return "source mismatch: non-WEB-DL vs WEB-DL"
        return ""

    @classmethod
    def _source_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        reason = cls._source_mismatch_reason(ctx, state)
        if not reason:
            return None
        cls._log_exclusion(ctx, reason, state.name)
        return True

    @staticmethod
    def _skip_resolution_check(ctx: _DupeFilterContext) -> bool:
        return ctx.is_dvd or "DVD" in ctx.target_source or ctx.is_dvdrip

    @staticmethod
    def _candidate_has_resolution(state: _PreparedDupe) -> bool:
        if state.res_id:
            return True
        return bool(
            re.search(
                r"\b(?:480|576|720|1080|2160)[pi]\b", state.name, re.IGNORECASE
            )
        )

    @staticmethod
    def _oldtoonsworld_enabled(ctx: _DupeFilterContext) -> bool:
        return all(
            (
                ctx.tracker_name == "OLDTOONSWORLD",
                not ctx.is_tv_pack,
                ctx.meta.category == "TV",
                bool(ctx.target_episode),
                bool(ctx.target_resolution),
            )
        )

    @staticmethod
    def _oldtoonsworld_same_season_episode(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if ctx.target_season_number is None:
            return False
        season_match = re.search(r"[sS](\d+)", state.name)
        if season_match is None:
            return False
        if int(season_match.group(1)) != ctx.target_season_number:
            return False
        return bool(re.search(r"[eE]\d{2}", state.name))

    @classmethod
    def _oldtoonsworld_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._oldtoonsworld_enabled(ctx):
            return None
        if not cls._oldtoonsworld_same_season_episode(ctx, state):
            return None
        if ctx.target_resolution.lower() in state.name.lower():
            return None
        cls._log_exclusion(
            ctx,
            f"OLDTOONSWORLD same-season episode resolution mismatch: expected '{ctx.target_resolution}'",
            state.name,
        )
        return False

    @classmethod
    def _resolution_mismatch(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> bool:
        if not ctx.target_resolution or not cls._candidate_has_resolution(
            state
        ):
            return False
        return ctx.target_resolution not in state.name

    @classmethod
    async def _resolution_hdr_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if cls._skip_resolution_check(ctx):
            return None
        if cls._resolution_mismatch(ctx, state):
            cls._log_exclusion(
                ctx,
                f"resolution '{ctx.target_resolution}' mismatch",
                state.name,
            )
            return True
        hdr_matches = await cls.has_matching_hdr(
            state.file_hdr, ctx.target_hdr, ctx.meta, tracker=ctx.tracker_name
        )
        if hdr_matches:
            return None
        cls._log_exclusion(
            ctx,
            f"HDR mismatch: Expected {ctx.target_hdr}, got {state.file_hdr}",
            state.name,
        )
        return True

    @classmethod
    def _dvd_resolution_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not ctx.is_dvd or ctx.tracker_name == "BEYONDHD":
            return None
        if not any(
            str(resolution) in state.name for resolution in (1080, 720, 2160)
        ):
            return None
        cls._log_exclusion(
            ctx, f"resolution '{ctx.target_resolution}' mismatch", state.name
        )
        return False

    @staticmethod
    def _remux_mismatch_reason(
        ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> str:
        upload_is_remux = "remux" in str(ctx.meta.name or "").lower()
        dupe_is_remux = "remux" in state.normalized.lower()
        logger.debug(
            f"[debug] Remux check: uuid_has_remux={upload_is_remux}, dupe_has_remux={dupe_is_remux}"
        )
        relation = (upload_is_remux, dupe_is_remux)
        if relation == (True, False):
            return "missing 'remux'"
        if relation == (False, True):
            return "dupe is remux but upload is not"
        return ""

    @classmethod
    def _remux_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        reason = cls._remux_mismatch_reason(ctx, state)
        if not reason:
            return None
        cls._log_exclusion(ctx, reason, state.name)
        return True

    @staticmethod
    def _generic_filename_enabled(ctx: _DupeFilterContext) -> bool:
        return not ctx.meta.is_disc and ctx.tracker_name not in {
            "ALPHARATIO",
            "RETROFLIX",
        }

    @classmethod
    def _generic_filename_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._generic_filename_enabled(ctx):
            return None
        local_file = cls._matching_local_filename(ctx, state, False)
        if local_file is None:
            return None
        logger.debug(f"[debug] Filename match candidate: {local_file}")
        cls._set_filename_match(ctx.meta, state)
        logger.debug(
            f"[debug] Filename match found: {ctx.meta.filename_match}"
        )
        cls._remember_match(ctx, state, "filename")
        cls._remember_match(ctx, state, "id")
        return False if cls._record_file_count_match(ctx, state) else None

    @staticmethod
    def _single_encode_size_enabled(ctx: _DupeFilterContext) -> bool:
        return all(
            (
                ctx.dupe_count == 1,
                ctx.meta.is_disc != "BDMV",
                ctx.tracker_name
                in {"AITHER", "BEYONDHD", "HAWKEUNO", "ONLYENCODES", "ULCX"},
                ctx.file_size is not None,
                "1080" in ctx.target_resolution,
                "x264" in ctx.video_encode_lower,
            )
        )

    @classmethod
    def _single_encode_size_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._single_encode_size_enabled(ctx):
            return None
        dupe_size = cls._coerce_int(state.size)
        if not dupe_size:
            return None
        size_difference = (cast(int, ctx.file_size) - dupe_size) / dupe_size
        logger.debug(
            f"Your size: {ctx.file_size}, Dupe size: {dupe_size}, Size difference: {size_difference:.4f}"
        )
        if size_difference < 0.20:
            return None
        cls._log_exclusion(
            ctx,
            f"Your file is significantly larger ({size_difference * 100:.2f}%)",
            state.name,
        )
        return True

    @staticmethod
    def _reelflix_enabled(ctx: _DupeFilterContext) -> bool:
        return all(
            (
                ctx.dupe_count == 1,
                ctx.meta.is_disc != "BDMV",
                ctx.tracker_name == "REELFLIX",
            )
        )

    @classmethod
    def _reelflix_decision(
        cls, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        if not cls._reelflix_enabled(ctx):
            return None
        tag = ctx.tag.strip()
        if not tag:
            return None
        if tag in state.normalized:
            return False
        cls._log_exclusion(
            ctx, f"Tag '{ctx.tag}' not found in normalized name", state.name
        )
        return True

    @staticmethod
    def _run_sync_handlers(
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        handlers: Sequence[SyncExclusionHandler],
    ) -> ExclusionDecision:
        for handler in handlers:
            decision = handler(ctx, state)
            if decision is not None:
                return decision
        return None

    @staticmethod
    async def _run_async_handlers(
        ctx: _DupeFilterContext,
        state: _PreparedDupe,
        handlers: Sequence[AsyncExclusionHandler],
    ) -> ExclusionDecision:
        for handler in handlers:
            decision = await handler(ctx, state)
            if decision is not None:
                return decision
        return None

    async def _generic_phase_one(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        decision = self._run_sync_handlers(
            ctx,
            state,
            (
                self._trumpable_flag_decision,
                self._disc_file_decision,
                self._repack_filter_decision,
                self._disc_size_decision,
                self._source_payload_cross_seed_decision,
                self._beyondhd_size_decision,
                self._beyondhd_name_decision,
            ),
        )
        if decision is not None:
            return decision
        return await self._hawkeuno_name_decision(ctx, state)

    async def _generic_phase_two(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        decision = self._run_sync_handlers(
            ctx,
            state,
            (
                self._special_keep_decision,
                self._aither_dvd_decision,
                self._source_decision,
                self._oldtoonsworld_decision,
            ),
        )
        if decision is not None:
            return decision
        return await self._resolution_hdr_decision(ctx, state)

    async def _generic_phase_three(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        decision = self._run_sync_handlers(
            ctx,
            state,
            (
                self._dvd_resolution_decision,
                self._remux_decision,
                self._generic_filename_decision,
            ),
        )
        if decision is not None:
            return decision
        return await self._tv_final_decision(ctx, state)

    async def _generic_phase_four(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        return self._run_sync_handlers(
            ctx,
            state,
            (self._single_encode_size_decision, self._reelflix_decision),
        )

    async def _prepared_dupe_decision(
        self, ctx: _DupeFilterContext, state: _PreparedDupe
    ) -> ExclusionDecision:
        handlers: tuple[AsyncExclusionHandler, ...] = (
            self._tv_initial_decision,
            self._game_decision,
            self._book_decision,
            self._generic_phase_one,
            self._generic_phase_two,
            self._generic_phase_three,
            self._generic_phase_four,
        )
        return await self._run_async_handlers(ctx, state, handlers)

    async def _process_dupe(
        self, ctx: _DupeFilterContext, entry: DupeEntry
    ) -> bool:
        preflight = await self._entry_preflight_decision(ctx, entry)
        if preflight is not None:
            return preflight
        state = await self._prepare_dupe(entry)
        self._debug_prepared_dupe(ctx, state)
        decision = await self._prepared_dupe_decision(ctx, state)
        if decision is not None:
            return decision
        if ctx.meta.debug:
            logger.debug(f"[cyan]Release PASSED all checks: {state.name}")
        return False

    @staticmethod
    def _same_release_group(ctx: _DupeFilterContext, name: str) -> bool:
        return bool(ctx.release_group) and name.rstrip().casefold().endswith(
            f"-{ctx.release_group}"
        )

    @classmethod
    async def _same_repack_base(
        cls, ctx: _DupeFilterContext, name: str
    ) -> bool:
        target = re.sub(
            r"\s+",
            " ",
            ctx.repack_pattern.sub(
                "", str(ctx.meta.name or ctx.meta.uuid or "")
            ),
        ).strip()
        candidate = re.sub(
            r"\s+", " ", ctx.repack_pattern.sub("", name)
        ).strip()
        normalized_target = re.sub(
            r"\s+", " ", await cls.normalize_filename(target)
        ).strip()
        normalized_candidate = re.sub(
            r"\s+", " ", await cls.normalize_filename(candidate)
        ).strip()
        return normalized_target == normalized_candidate

    @classmethod
    async def _find_repack_entry(
        cls,
        ctx: _DupeFilterContext,
        entries: list[DupeEntry],
        *,
        require_repack: bool,
    ) -> DupeEntry | None:
        for entry in entries:
            name = str(entry.get("name", ""))
            has_repack = bool(ctx.repack_pattern.search(name))
            if has_repack != require_repack:
                continue
            if not cls._same_release_group(ctx, name):
                continue
            if await cls._same_repack_base(ctx, name):
                return entry
        return None

    @classmethod
    async def _apply_repack_postprocess(
        cls, ctx: _DupeFilterContext, entries: list[DupeEntry]
    ) -> None:
        if not ctx.prefers_repack:
            return
        preferred = await cls._find_repack_entry(
            ctx, entries, require_repack=True
        )
        if preferred is not None:
            ctx.meta[f"{ctx.tracker_name}_preferred_repack"] = preferred
            return
        if not ctx.preferred_upload_is_repack:
            return
        replaced = await cls._find_repack_entry(
            ctx, entries, require_repack=False
        )
        if replaced is not None:
            ctx.meta[f"{ctx.tracker_name}_repack_replaces"] = replaced

    @staticmethod
    def _log_exact_match_summary(
        ctx: _DupeFilterContext,
        processed: list[DupeEntry],
        filtered: list[DupeEntry],
    ) -> None:
        if not ctx.is_exact_match_only:
            return
        if processed and not filtered:
            logger.info(
                f"{ctx.tracker_name}: related releases found, but no exact renamed release was detected."
            )
            logger.info(f"{ctx.tracker_name}: continuing upload.")
            return
        if filtered:
            logger.info(
                f"{ctx.tracker_name}: exact existing release detected from matching files and size."
            )

    @staticmethod
    def _limited_filtered_dupe(dupe: DupeEntry) -> dict[str, Any]:
        limited = cast(
            dict[str, Any], Redaction.redact_private_info(dupe)
        ).copy()
        files = [
            str(value) for value in cast(list[Any], limited.get("files", []))
        ]
        if len(files) > 10:
            original_files = cast(list[Any], dupe.get("files", []))
            limited["files"] = [
                *files[:10],
                f"... and {len(original_files) - 10} more files",
            ]
        description = limited.get("description")
        if isinstance(description, str) and len(description) > 200:
            limited["description"] = description[:200] + "..."
        return limited

    @staticmethod
    def _should_debug_filtered(
        ctx: _DupeFilterContext,
        processed: list[DupeEntry],
        filtered: list[DupeEntry],
    ) -> bool:
        return all(
            (
                bool(filtered),
                not ctx.meta.unattended,
                bool(ctx.meta.debug),
                len(processed) > 1,
            )
        )

    @classmethod
    def _debug_filtered_dupes(
        cls,
        ctx: _DupeFilterContext,
        processed: list[DupeEntry],
        filtered: list[DupeEntry],
    ) -> None:
        if not cls._should_debug_filtered(ctx, processed, filtered):
            return
        logger.debug(f"[yellow]Filtered dupes on {ctx.tracker_name}: ")
        logger.debug([cls._limited_filtered_dupe(dupe) for dupe in filtered])

    async def filter_dupes(
        self, dupes: Sequence[DupeInput], meta: Meta, tracker_name: str
    ) -> list[DupeEntry]:
        """Filter duplicates while preserving tracker-specific match metadata."""
        self._debug_pre_filtered(dupes, meta, tracker_name)
        self._reset_filter_meta(meta)
        processed = self._normalize_dupes(dupes)
        ctx = await self._build_filter_context(dupes, meta, tracker_name)
        filtered = [
            entry
            for entry in processed
            if not await self._process_dupe(ctx, entry)
        ]
        await self._apply_repack_postprocess(ctx, filtered)
        self._log_exact_match_summary(ctx, processed, filtered)
        self._debug_filtered_dupes(ctx, processed, filtered)
        return filtered

    @staticmethod
    def _exact_local_files(meta: Meta) -> list[str]:
        if not meta.filelist or meta.is_disc:
            return []
        return [Path(str(file)).name.lower() for file in meta.filelist if file]

    @staticmethod
    def _mediainfo_first_track_size(meta: Meta) -> Any:
        media = meta.mediainfo.get("media", {})
        if not isinstance(media, Mapping):
            return None
        tracks = cast(Mapping[str, Any], media).get("track", [])
        if not isinstance(tracks, list) or not tracks:
            return None
        first = cast(list[Any], tracks)[0]
        if not isinstance(first, Mapping):
            return None
        return cast(Mapping[str, Any], first).get("FileSize")

    @staticmethod
    def _exact_local_size(meta: Meta) -> int | None:
        from src.services.upload_decision_service import parse_size_to_bytes

        size = parse_size_to_bytes(meta.source_size)
        if size is not None:
            return size
        if meta.is_disc or not meta.mediainfo:
            return None
        return parse_size_to_bytes(
            DupeChecker._mediainfo_first_track_size(meta)
        )

    @staticmethod
    def _exact_candidate_file_list(raw_files: list[Any]) -> list[str]:
        return [Path(str(file)).name.lower() for file in raw_files if file]

    @staticmethod
    def _exact_candidate_file_string(raw_files: str) -> list[str]:
        return [
            Path(file.strip()).name.lower()
            for file in raw_files.split(",")
            if file.strip()
        ]

    @classmethod
    def _exact_candidate_files(cls, candidate: Mapping[str, Any]) -> list[str]:
        raw_files = candidate.get("files", [])
        if isinstance(raw_files, list):
            return cls._exact_candidate_file_list(cast(list[Any], raw_files))
        if isinstance(raw_files, str) and raw_files:
            return cls._exact_candidate_file_string(raw_files)
        return []

    @staticmethod
    def _candidate_file_count(
        candidate: Mapping[str, Any], files: list[str]
    ) -> int:
        value = candidate.get("file_count")
        if value is None:
            return len(files)
        try:
            return int(value)
        except TypeError, ValueError:
            return len(files)

    @staticmethod
    def _same_optional_size(
        local_size: int | None, candidate_size: int | None
    ) -> bool:
        return (
            local_size is not None
            and candidate_size is not None
            and local_size == candidate_size
        )

    @staticmethod
    def _exact_names_match(candidate: Mapping[str, Any], meta: Meta) -> bool:
        candidate_name = str(candidate.get("name", "")).strip().lower()
        local_name = str(meta.name or "").strip().lower()
        if not candidate_name or not local_name:
            return False
        return candidate_name == local_name

    @staticmethod
    def _exact_size_compatible(
        same_size: bool, local_size: int | None, candidate_size: int | None
    ) -> bool:
        if same_size:
            return True
        return local_size is None or candidate_size is None

    @classmethod
    def _exact_name_fallback(
        cls,
        candidate: Mapping[str, Any],
        meta: Meta,
        same_size: bool,
        local_size: int | None,
        candidate_size: int | None,
    ) -> bool:
        return cls._exact_names_match(
            candidate, meta
        ) and cls._exact_size_compatible(same_size, local_size, candidate_size)

    @staticmethod
    def _exact_file_lists_match(
        local_files: list[str], candidate_files: list[str], same_size: bool
    ) -> bool | None:
        if not local_files or not candidate_files:
            return None
        return sorted(local_files) == sorted(candidate_files) and same_size

    @staticmethod
    def _exact_counts_match(
        local_files: list[str], candidate_count: int, same_size: bool
    ) -> bool:
        if not local_files or candidate_count <= 0 or not same_size:
            return False
        return len(local_files) == candidate_count

    @staticmethod
    def _exact_disc_size_match(
        local_files: list[str], candidate_files: list[str], same_size: bool
    ) -> bool:
        return not local_files and not candidate_files and same_size

    @classmethod
    async def is_exact_match(
        cls, candidate: dict[str, Any] | DupeEntry, meta: Meta
    ) -> bool:
        """Check if candidate torrent is an exact match / exact renamed release of local upload."""
        from src.services.upload_decision_service import parse_size_to_bytes

        candidate_map = cast(Mapping[str, Any], candidate)
        local_files = cls._exact_local_files(meta)
        candidate_files = cls._exact_candidate_files(candidate_map)
        local_size = cls._exact_local_size(meta)
        candidate_size = parse_size_to_bytes(candidate_map.get("size"))
        same_size = cls._same_optional_size(local_size, candidate_size)
        file_match = cls._exact_file_lists_match(
            local_files, candidate_files, same_size
        )
        if file_match is not None:
            return file_match
        candidate_count = cls._candidate_file_count(
            candidate_map, candidate_files
        )
        if cls._exact_counts_match(local_files, candidate_count, same_size):
            return True
        if cls._exact_disc_size_match(local_files, candidate_files, same_size):
            return True
        return cls._exact_name_fallback(
            candidate_map, meta, same_size, local_size, candidate_size
        )

    @staticmethod
    async def normalize_filename(
        filename: str | MutableMapping[str, Any],
    ) -> str:
        if isinstance(filename, dict):
            filename = str(filename.get("name", ""))
        if not isinstance(filename, str):
            raise ValueError(
                f"Expected a string or a dictionary with a 'name' key, but got: {type(filename)}"
            )
        return (
            filename.lower()
            .replace("-", " -")
            .replace(" ", " ")
            .replace(".", " ")
        )

    @staticmethod
    def _season_number(value: str | int | None) -> int | None:
        match = re.search(r"[sS](\d+)", str(value))
        return int(match.group(1)) if match else None

    @staticmethod
    def _daily_episode_pattern(value: str | int | None) -> str | None:
        match = re.search(
            r"(?<!\d)((?:19|20)\d{2})[.\-_/\s](\d{1,2})[.\-_/\s](\d{1,2})(?!\d)",
            str(value or ""),
        )
        if match is None:
            return None
        year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        return rf"(?<!\d){year}[.\-_/\s]?{month:02d}[.\-_/\s]?{day:02d}(?!\d)"

    @staticmethod
    def _episode_numbers(value: str | int | None) -> list[int]:
        if not value:
            return []
        return [int(item) for item in re.findall(r"\d+", str(value))]

    @staticmethod
    def _season_pattern(season: int | None) -> str | None:
        return rf"[sS]{season:02}" if season is not None else None

    @staticmethod
    def _season_matches(filename: str, season_pattern: str | None) -> bool:
        return bool(
            season_pattern
            and re.search(season_pattern, filename, re.IGNORECASE)
        )

    @staticmethod
    def _is_season_pack_name(filename: str) -> bool:
        return not bool(re.search(r"[eE]\d{2}", filename, re.IGNORECASE))

    @staticmethod
    def _episode_pattern_match(filename: str, episodes: list[int]) -> bool:
        patterns = [rf"[eE]{episode:02}" for episode in episodes]
        return any(
            re.search(pattern, filename, re.IGNORECASE) for pattern in patterns
        )

    @classmethod
    def _regular_season_episode_match(
        cls,
        filename: str,
        season_pattern: str | None,
        episodes: list[int],
    ) -> tuple[bool, bool]:
        is_pack = cls._is_season_pack_name(filename)
        season_matches = cls._season_matches(filename, season_pattern)
        if not episodes:
            return season_matches and is_pack, season_matches
        if not season_matches:
            return False, False
        if is_pack:
            return True, True
        return cls._episode_pattern_match(filename, episodes), False

    @classmethod
    async def is_season_episode_match(
        cls,
        filename: str,
        target_season: str | int | None,
        target_episode: str | int | None,
    ) -> tuple[bool, bool]:
        """Check if *filename* matches the requested season/episode identity."""
        daily_pattern = cls._daily_episode_pattern(target_episode)
        if daily_pattern is not None:
            return bool(
                re.search(daily_pattern, filename, re.IGNORECASE)
            ), False
        season = cls._season_number(target_season)
        return cls._regular_season_episode_match(
            filename,
            cls._season_pattern(season),
            cls._episode_numbers(target_episode),
        )

    @staticmethod
    async def refine_hdr_terms(hdr: str | None) -> set[str]:
        """
        Normalize HDR terms for consistent comparison.
        Simplifies all HDR entries to 'HDR' and DV entries to 'DV'.
        """
        if hdr is None:
            return set()
        hdr_upper = hdr.upper()
        terms: set[str] = set()
        if "DV" in hdr_upper or "DOVI" in hdr_upper:
            terms.add("DV")
        if "HDR" in hdr_upper:  # Any HDR-related term is normalized to 'HDR'
            terms.add("HDR")
        return terms

    @staticmethod
    def _has_dv_hdr_term(hdr_set: set[str]) -> bool:
        return any(term == "DV" or "DV" in term for term in hdr_set)

    @staticmethod
    def _dv_implies_hdr(meta: Meta, tracker_name: str | None) -> bool:
        if tracker_name == "ANTHELION":
            return True
        return "web" not in str(meta.type).lower()

    @classmethod
    def _simplify_hdr(
        cls, hdr_set: set[str], meta: Meta, tracker_name: str | None = None
    ) -> set[str]:
        simplified: set[str] = set()
        if hdr_set.intersection({"HDR", "HDR10", "HDR10+"}):
            simplified.add("HDR")
        if not cls._has_dv_hdr_term(hdr_set):
            return simplified
        simplified.add("DV")
        if cls._dv_implies_hdr(meta, tracker_name):
            simplified.add("HDR")
        return simplified

    @staticmethod
    def _collapse_dv_hdr(value: set[str]) -> set[str]:
        return {"HDR"} if value == {"DV", "HDR"} else value

    @classmethod
    async def has_matching_hdr(
        cls,
        file_hdr: set[str],
        target_hdr: set[str],
        meta: Meta,
        tracker: str | None = None,
    ) -> bool:
        """Check if the HDR terms match or are tracker-compatible."""
        file_simple = cls._collapse_dv_hdr(
            cls._simplify_hdr(file_hdr, meta, tracker)
        )
        target_simple = cls._collapse_dv_hdr(
            cls._simplify_hdr(target_hdr, meta, tracker)
        )
        return file_simple == target_simple


async def filter_dupes(
    dupes: Sequence[DupeInput],
    meta: Meta,
    tracker_name: str,
    config: dict[str, Any],
) -> list[DupeEntry]:
    return await DupeChecker(config).filter_dupes(dupes, meta, tracker_name)


async def normalize_filename(filename: str | MutableMapping[str, Any]) -> str:
    return await DupeChecker.normalize_filename(filename)


async def is_season_episode_match(
    filename: str,
    target_season: str | int | None,
    target_episode: str | int | None,
) -> tuple[bool, bool]:
    return await DupeChecker.is_season_episode_match(
        filename, target_season, target_episode
    )


async def refine_hdr_terms(hdr: str | None) -> set[str]:
    return await DupeChecker.refine_hdr_terms(hdr)


async def has_matching_hdr(
    file_hdr: set[str],
    target_hdr: set[str],
    meta: Meta,
    tracker: str | None = None,
) -> bool:
    return await DupeChecker.has_matching_hdr(
        file_hdr, target_hdr, meta, tracker=tracker
    )
