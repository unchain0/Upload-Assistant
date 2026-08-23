# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import re
from collections.abc import Callable, Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Never, cast

import anitopy
import cli_ui
import guessit
import httpx

from src.domain_models.errors import OperationAbortedError
from src.domain_models.processing import *  # noqa: F403
from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.filesystem.tags import get_tag
from src.integrations.observability.runtime_support import (
    console,
    logger,
    prompt_in_thread,
)

guessit_module: Any = cast(Any, guessit)
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


def _guessit_data(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return guessit_fn(value, options)


def _anitopy_parse(value: str) -> dict[str, Any]:
    anitopy_any = cast(Any, anitopy)
    return cast(dict[str, Any], anitopy_any.parse(value) or {})


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _clear_episode_metadata(meta: Meta) -> None:
    meta.auto_episode_title = None
    meta.overview_meta = None
    meta.episode_airdate = None
    meta.episode_name = ""
    meta.episode_overview = ""
    if not meta.manual_episode_title:
        meta.episode_title = ""
    meta.episode_tmdb_data = {}
    meta.tmdb_episode_data = None
    meta.tvdb_episode_data = {}
    meta.tvdb_episode_id = None
    meta.tvdb_episode_int = None
    meta.tvdb_episode_name = None
    meta.tvdb_episode_year = ""
    meta.tvdb_episode = None
    meta.tvdb_imdb_id = None
    meta.tvdb_overview = None
    meta.tvdb_season_name = ""
    meta.tvdb_season_int = None
    meta.tvdb_season = None
    meta.tvmaze_episode_data = {}
    meta.we_asked_tvmaze = False
    meta.we_checked_tmdb = False
    meta.we_checked_tvdb = False


_VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".vob",
    }
)
_EPISODE_IDENTITY_PATTERN = re.compile(
    r"(?i)(?<![^\W_])S([0-9]{1,3})E([0-9]{1,4})(?![^\W_])"
)


def _has_manual_episode_identity(meta: Meta) -> bool:
    return bool(
        meta.manual_date
        or meta.manual_season is not None
        or meta.manual_episode is not None
    )


def _eligible_for_filename_episode_sync(meta: Meta) -> bool:
    if meta.category != "TV":
        return False
    if bool(meta.is_disc) or meta.tv_pack:
        return False
    return not _has_manual_episode_identity(meta)


def _is_video_file_path(path: object) -> bool:
    if not isinstance(path, (str, Path)):
        return False
    return Path(path).suffix.lower() in _VIDEO_EXTENSIONS


def _single_video_file(meta: Meta) -> Path | None:
    filelist = meta.filelist if isinstance(meta.filelist, list) else []
    video_files = [
        Path(path) for path in filelist if _is_video_file_path(path)
    ]
    if len(video_files) != 1:
        return None
    return video_files[0]


def _guessit_allows_single_episode(filename: str) -> bool:
    try:
        guessed_episodes = _guessit_data(filename).get("episode")
    except Exception:
        return False
    return not isinstance(guessed_episodes, list)


def _episode_identity_from_filename(filename: str) -> tuple[int, int] | None:
    if len(filename) > 1024:
        return None
    if not _guessit_allows_single_episode(filename):
        return None
    matches = _EPISODE_IDENTITY_PATTERN.findall(filename)
    if len(matches) != 1:
        return None
    season_value, episode_value = matches[0]
    return int(season_value), int(episode_value)


def _apply_episode_identity(
    meta: Meta, season_int: int, episode_int: int
) -> None:
    _clear_episode_metadata(meta)
    meta.season_int = season_int
    meta.episode_int = episode_int
    meta.season = f"S{season_int:02d}"
    meta.episode = f"E{episode_int:02d}"


def sync_single_episode_from_filename(meta: Meta) -> bool:
    if not _eligible_for_filename_episode_sync(meta):
        return False
    video_file = _single_video_file(meta)
    if video_file is None:
        return False
    identity = _episode_identity_from_filename(video_file.name)
    if identity is None:
        return False
    if identity == (meta.season_int, meta.episode_int):
        return False
    _apply_episode_identity(meta, *identity)
    return True


class SeasonEpisodeManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.tmdb_manager = TmdbManager(config)

    async def get_season_episode(self, video: str, meta: Meta) -> Meta:
        if meta.category == "TV":
            previous_identity = (meta.season_int, meta.episode_int)
            filelist = cast(list[str], meta.filelist)
            meta.tv_pack = False
            is_daily = False
            season_int = 1
            episode_int = 0
            season = "S01"
            episode = ""
            romaji = ""
            eng_title = ""
            anilist_episodes = 0
            if not meta.anime:
                try:
                    daily_match = re.search(
                        r"\d{4}[-\.]\d{2}[-\.]\d{2}", video
                    )
                    if (
                        meta.manual_date or daily_match
                    ) and not meta.manual_season:
                        # Handle daily episodes
                        # The user either provided the --daily argument or a date was found in the filename

                        if (
                            meta.manual_date is None
                            and daily_match is not None
                        ):
                            meta.manual_date = daily_match.group().replace(
                                ".", "-"
                            )
                        is_daily = True
                        guess_data = _guessit_data(video)
                        guess_date_raw = meta.manual_date or guess_data.get(
                            "date"
                        )
                        guess_date = (
                            str(guess_date_raw) if guess_date_raw else ""
                        )
                        tmdb_id_value = _safe_int(meta.tmdb_id, 0)
                        (
                            season_int,
                            episode_int,
                        ) = await self.tmdb_manager.daily_to_tmdb_season_episode(
                            tmdb_id_value, guess_date
                        )

                        season = f"S{str(season_int).zfill(2)}"
                        episode = f"E{str(episode_int).zfill(2)}"
                        # For daily shows, pass the supplied date as the episode title
                        # Season and episode will be stripped later to conform with standard daily episode naming format
                        meta.daily_episode_title = meta.manual_date or ""

                    else:
                        try:
                            guess_year = str(
                                _guessit_data(video).get("year") or ""
                            )
                        except Exception:
                            guess_year = ""
                        try:
                            guess_data = _guessit_data(video)
                            season_guess = str(guess_data.get("season") or "")
                            if season_guess == guess_year:
                                if f"s{season_guess}" in video.lower():
                                    season_int = int(season_guess)
                                    season = "S" + str(season_int).zfill(2)
                                else:
                                    season_int = 1
                                    season = "S01"
                            else:
                                season_int = int(guess_data.get("season") or 1)
                                season = "S" + str(season_int).zfill(2)
                        except Exception:
                            logger.info(
                                "[bold yellow]There was an error guessing the season number. Guessing S01. Use [bold green]--season #[/bold green] to correct if needed"
                            )
                            season_int = 1
                            season = "S01"

                except Exception:
                    console.print_exception()
                    season_int = 1
                    season = "S01"

                try:
                    if is_daily is not True:
                        episodes = ""
                        if len(filelist) == 1:
                            guess_data = _guessit_data(video)
                            episodes = guess_data.get("episode")
                            if isinstance(episodes, list):
                                episode = ""
                                episodes_list = episodes
                                for item in episodes_list:
                                    ep = str(item).zfill(2)
                                    episode += f"E{ep}"
                                episode_int = (
                                    _safe_int(episodes_list[0], 0)
                                    if episodes_list
                                    else 0
                                )
                            else:
                                episode_int = _safe_int(episodes, 0)
                                episode = (
                                    "E" + str(episode_int).zfill(2)
                                    if episodes is not None
                                    else ""
                                )
                        else:
                            episode = ""
                            episode_int = 0
                            meta.tv_pack = True
                except Exception:
                    episode = ""
                    episode_int = 0
                    meta.tv_pack = True

            else:
                # If Anime
                # if the mal id is set, then we've already run get_romaji in tmdb.py
                if meta.mal_id == 0 and meta.category == "TV":
                    parsed = _anitopy_parse(Path(video).name)
                    (
                        romaji,
                        mal_id,
                        eng_title,
                        season_year,
                        anilist_episodes,
                        meta.demographic,
                    ) = await self.tmdb_manager.get_romaji(
                        str(parsed.get("anime_title", "")),
                        _safe_int(meta.mal_id, 0),
                        meta,
                    )
                    mal_id_value = _safe_int(mal_id, 0)
                    if mal_id_value:
                        meta.mal_id = mal_id_value
                    anilist_episodes = _safe_int(anilist_episodes, 0)
                    if meta.tmdb_id == 0:
                        year = str(parsed.get("anime_year") or season_year)
                        guess_title = _guessit_data(
                            str(parsed.get("anime_title", "")),
                            {"excludes": ["country", "language"]},
                        ).get("title", "")
                        (
                            tmdb_id_value,
                            category_value,
                        ) = await self.tmdb_manager.get_tmdb_id(
                            str(guess_title),
                            year,
                            meta.category,
                            meta.filename,
                        )
                        meta.tmdb_id = tmdb_id_value
                        meta.category = category_value
                    # meta = await tmdb_other_meta(meta)
                if meta.mal_id != 0 and meta.category == "TV":
                    parsed = _anitopy_parse(Path(video).name)
                    tag = str(parsed.get("release_group", ""))
                    if tag != "" and meta.tag is None:
                        meta.tag = f"-{tag}"
                    if len(filelist) == 1:
                        try:
                            guess_data = _guessit_data(video)
                            episodes = parsed.get(
                                "episode_number",
                                guess_data.get("episode", "1"),
                            )
                            if (
                                not isinstance(episodes, list)
                                and not str(episodes).isnumeric()
                            ):
                                episodes = guess_data.get("episode")
                            if isinstance(episodes, list):
                                episodes_list = episodes
                                episode_int = (
                                    _safe_int(episodes_list[0], 1)
                                    if episodes_list
                                    else 1
                                )
                                episode = "".join(
                                    [
                                        f"E{str(_safe_int(item, 0)).zfill(2)}"
                                        for item in episodes_list
                                    ]
                                )
                            else:
                                episode_int = _safe_int(episodes, 1)
                                episode = f"E{str(episode_int).zfill(2)}"
                        except Exception:
                            episode_int = 1
                            episode = "E01"

                            if meta.uuid:
                                # Look for episode patterns in uuid
                                episode_patterns = [
                                    r"[Ee](\d+)[Ee](\d+)",
                                    r"[Ee](\d+)",
                                    r"[Ee]pisode[\s_]*(\d+)",
                                    r"[\s_\-](\d+)[\s_\-]",
                                    r"[\s_\-](\d+)$",
                                    r"^(\d+)[\s_\-]",
                                ]

                                for pattern in episode_patterns:
                                    match = re.search(
                                        pattern, meta.uuid, re.IGNORECASE
                                    )
                                    if match:
                                        try:
                                            episode_int = int(match.group(1))
                                            episode = (
                                                f"E{str(episode_int).zfill(2)}"
                                            )
                                            break
                                        except ValueError, IndexError:
                                            continue

                            if episode_int == 1:  # Still using fallback
                                logger.info(
                                    "[bold yellow]There was an error guessing the episode number. Guessing E01. Use [bold green]--episode #[/bold green] to correct if needed"
                                )

                            await asyncio.sleep(1.5)
                    else:
                        episode = ""
                        episode_int = 0  # Ensure it's an integer
                        meta.tv_pack = True

                    try:
                        if meta.season_int:
                            season_int = _safe_int(meta.season_int, 1)
                        else:
                            guess_data = _guessit_data(video)
                            season_value = parsed.get(
                                "anime_season", guess_data.get("season", "1")
                            )
                            season_int = _safe_int(season_value, 1)
                        season = f"S{season_int:02d}"
                    except Exception:
                        try:
                            if episode_int >= anilist_episodes:
                                params = {
                                    "id": str(meta.tvdb_id),
                                    "origin": "tvdb",
                                    "absolute": str(episode_int),
                                }
                                url = "https://thexem.info/map/single"
                                async with httpx.AsyncClient(
                                    timeout=30.0
                                ) as client:
                                    response = (
                                        await client.post(url, params=params)
                                    ).json()
                                if response["result"] == "failure":
                                    raise XEMNotFoundError  # noqa: F405
                                logger.debug(
                                    f"[cyan]TheXEM Absolute -> Standard[/cyan]\n{response}"
                                )
                                season_int = int(
                                    response["data"]["scene"]["season"]
                                )  # Convert to integer
                                season = f"S{str(season_int).zfill(2)}"
                                if len(filelist) == 1:
                                    episode_int = int(
                                        response["data"]["scene"]["episode"]
                                    )  # Convert to integer
                                    episode = f"E{str(episode_int).zfill(2)}"
                            else:
                                season_int = 1  # Default to 1 if error occurs
                                season = "S01"
                                names_url = f"https://thexem.info/map/names?origin=tvdb&id={meta.tvdb_id!s}"
                                async with httpx.AsyncClient(
                                    timeout=30.0
                                ) as client:
                                    names_response = (
                                        await client.get(names_url)
                                    ).json()
                                logger.debug(
                                    f"[cyan]Matching Season Number from TheXEM\n{names_response}"
                                )
                                difference: float = 0.0
                                if names_response["result"] == "success":
                                    for season_num, values in names_response[
                                        "data"
                                    ].items():
                                        for lang, names in values.items():
                                            if lang == "jp":
                                                for name in names:
                                                    romaji_check = re.sub(
                                                        r"[^0-9a-zA-Z\[\\]]+",
                                                        "",
                                                        romaji.lower().replace(
                                                            " ", ""
                                                        ),
                                                    )
                                                    name_check = re.sub(
                                                        r"[^0-9a-zA-Z\[\\]]+",
                                                        "",
                                                        name.lower().replace(
                                                            " ", ""
                                                        ),
                                                    )
                                                    diff = SequenceMatcher(
                                                        None,
                                                        romaji_check,
                                                        name_check,
                                                    ).ratio()
                                                    if (
                                                        romaji_check
                                                        in name_check
                                                        and diff >= difference
                                                    ):
                                                        season_int = (
                                                            int(season_num)
                                                            if season_num
                                                            != "all"
                                                            else 1
                                                        )  # Convert to integer
                                                        season = f"S{str(season_int).zfill(2)}"
                                                        difference = diff
                                            if lang == "us":
                                                for name in names:
                                                    eng_check = re.sub(
                                                        r"[^0-9a-zA-Z\[\\]]+",
                                                        "",
                                                        eng_title.lower().replace(
                                                            " ", ""
                                                        ),
                                                    )
                                                    name_check = re.sub(
                                                        r"[^0-9a-zA-Z\[\\]]+",
                                                        "",
                                                        name.lower().replace(
                                                            " ", ""
                                                        ),
                                                    )
                                                    diff = SequenceMatcher(
                                                        None,
                                                        eng_check,
                                                        name_check,
                                                    ).ratio()
                                                    if (
                                                        eng_check in name_check
                                                        and diff >= difference
                                                    ):
                                                        season_int = (
                                                            int(season_num)
                                                            if season_num
                                                            != "all"
                                                            else 1
                                                        )  # Convert to integer
                                                        season = f"S{str(season_int).zfill(2)}"
                                                        difference = diff
                                else:
                                    raise XEMNotFoundError  # noqa: F405
                        except Exception:
                            if meta.debug:
                                console.print_exception()
                            try:
                                season_int = int(
                                    _guessit_data(video).get("season", "1")
                                )
                                season = f"S{season_int:02d}"
                            except Exception:
                                season_int = 1  # Default to 1 if error occurs
                                season = "S01"
                            logger.info(
                                f"[bold yellow]{meta.title} does not exist on thexem, guessing {season}"
                            )
                            logger.info(
                                f"[bold yellow]If [green]{season}[/green] is incorrect, use --season to correct"
                            )
                            await asyncio.sleep(3)
                else:
                    logger.info(
                        "[bold red]Error determining if TV show is anime or not[/bold red]"
                    )
                    logger.info(
                        "[bold yellow]Set manual season and episode[/bold yellow]"
                    )
                    season_int = 1
                    season = "S01"
                    episode_int = 1
                    episode = "E01"

            if meta.manual_season is None:
                meta.season = season
            else:
                manual_season_str = (
                    str(meta.manual_season).lower().replace("s", "")
                )
                meta.daily_episode_title = (
                    ""  # Clear daily episode title if manual season is set
                )
                season_int = _safe_int(manual_season_str, 1)
                meta.season = f"S{manual_season_str.zfill(2)}"
            if meta.manual_episode is None:
                meta.episode = episode
            else:
                manual_episode_str = (
                    str(meta.manual_episode).lower().replace("e", "")
                )
                episode_int = _safe_int(manual_episode_str, 0)
                meta.episode = f"E{manual_episode_str.zfill(2)}"
                meta.tv_pack = False

            # if " COMPLETE " in Path(video).name.replace('.', ' '):
            #     meta.season = "COMPLETE"
            meta.season_int = season_int
            meta.episode_int = episode_int
            if previous_identity != (season_int, episode_int):
                _clear_episode_metadata(meta)

            # Manual episode title
            if "manual_episode_title" in meta and meta.manual_episode_title:
                meta.episode_title = meta.manual_episode_title

            # Guess the part of the episode (if available)
            meta.part = ""
            if meta.tv_pack == 1:
                part = _guessit_data(str(Path(video).parent)).get("part")
                meta.part = f"Part {part}" if part else ""  # pyrefly: ignore [bad-assignment]

        return meta

    @staticmethod
    def _missing_episode_labels(
        completeness: Mapping[str, Any],
    ) -> list[str]:
        try:
            return [
                f"S{s:02d}E{e:02d}"
                for s, e in completeness["missing_episodes"]
            ]
        except ValueError:
            logger.error(
                "[red]Error determining missing episodes, you should double check the pack manually."
            )
            return ["Unknown"]

    @staticmethod
    def _log_incomplete_pack(missing_list: list[str], meta: Meta) -> None:
        if "Unknown" in missing_list:
            logger.warning(
                "[red]Warning: Season pack appears incomplete (missing episodes could not be determined)."
            )
        else:
            logger.warning("[red]Warning: Season pack appears incomplete!")
            logger.info(f"[yellow]Missing episodes: {', '.join(missing_list)}")
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                "[yellow]Unattended mode: continuing despite incomplete season pack (no confirmation)."
            )

    @staticmethod
    def _show_file_range(filelist: list[Any], start: int, end: int) -> int:
        batch = filelist[start:end]
        for index, file in enumerate(batch, start=start + 1):
            logger.info(f"[cyan]  {index:2d}. {Path(file).name}")
        return start + len(batch)

    @staticmethod
    def _should_prompt_more(
        files_shown: int, total_files: int, meta: Meta
    ) -> bool:
        if files_shown >= total_files:
            return False
        return not meta.unattended or meta.unattended_confirm

    @staticmethod
    async def _pack_file_prompt(remaining_files: int, batch_size: int) -> str:
        if remaining_files > batch_size:
            response = await prompt_in_thread(
                cli_ui.ask_string,
                f"Show (n)ext {batch_size} files, (a)ll remaining files, (c)ontinue with incomplete pack, or (q)uit? (n/a/c/Q): ",
            )
        else:
            response = await prompt_in_thread(
                cli_ui.ask_string,
                f"Show (a)ll remaining {remaining_files} files, (c)ontinue with incomplete pack, or (q)uit? (a/c/Q): ",
            )
        return (response or "").lower()

    @staticmethod
    def _abort_incomplete_pack() -> Never:
        logger.info(
            "[red]Aborting torrent creation due to incomplete season pack"
        )
        raise OperationAbortedError(
            "Torrent creation cancelled because the season pack is incomplete."
        ) from None

    @classmethod
    def _apply_pack_file_response(
        cls,
        response: str,
        filelist: list[Any],
        files_shown: int,
        batch_size: int,
        remaining_files: int,
    ) -> tuple[int, bool]:
        if response == "n" and remaining_files > batch_size:
            return (
                cls._show_file_range(
                    filelist, files_shown, files_shown + batch_size
                ),
                False,
            )
        if response == "a":
            return cls._show_file_range(
                filelist, files_shown, len(filelist)
            ), False
        if response == "c":
            return files_shown, True
        return cls._abort_incomplete_pack()

    @classmethod
    async def _review_pack_files(cls, meta: Meta) -> bool:
        filelist = list(meta.filelist)
        batch_size = 15
        logger.info(f"[cyan]Filelist ({len(filelist)} files):")
        files_shown = cls._show_file_range(filelist, 0, batch_size)
        while cls._should_prompt_more(files_shown, len(filelist), meta):
            remaining_files = len(filelist) - files_shown
            logger.info(f"[yellow]... and {remaining_files} more files")
            response = await cls._pack_file_prompt(remaining_files, batch_size)
            files_shown, just_go = cls._apply_pack_file_response(
                response,
                filelist,
                files_shown,
                batch_size,
                remaining_files,
            )
            if just_go:
                return True
        return False

    @staticmethod
    def _needs_pack_confirmation(meta: Meta, just_go: bool) -> bool:
        if just_go:
            return False
        return not meta.unattended or meta.unattended_confirm

    @classmethod
    async def _confirm_incomplete_pack(cls, meta: Meta, just_go: bool) -> None:
        if not cls._needs_pack_confirmation(meta, just_go):
            return
        response = await prompt_in_thread(
            cli_ui.ask_string,
            "Continue with incomplete season pack? (y/N): ",
        )
        if (response or "").lower() != "y":
            cls._abort_incomplete_pack()

    @classmethod
    async def _review_incomplete_pack(
        cls, completeness: Mapping[str, Any], meta: Meta
    ) -> None:
        missing_list = cls._missing_episode_labels(completeness)
        cls._log_incomplete_pack(missing_list, meta)
        if "Unknown" in missing_list:
            return
        just_go = await cls._review_pack_files(meta)
        await cls._confirm_incomplete_pack(meta, just_go)

    @staticmethod
    def _log_pack_tags(completeness: Mapping[str, Any]) -> None:
        if completeness["consistent_tags"]:
            return
        logger.warning(
            "[yellow]Warning: Multiple group tags detected in season pack!"
        )
        tags_found = cast(Mapping[str, list[str]], completeness["tags_found"])
        for tag, files in tags_found.items():
            logger.info(f"[cyan]Tag: {tag} found in files:")
            for file in files:
                logger.info(f"[cyan]  - {file}")

    async def check_season_pack_completeness(self, meta: Meta) -> None:
        completeness = cast(
            Mapping[str, Any], await self.check_season_pack_detail(meta)
        )
        if completeness["complete"]:
            logger.debug("[green]Season pack completeness verified")
        else:
            await self._review_incomplete_pack(completeness, meta)
        self._log_pack_tags(completeness)

    @staticmethod
    def _empty_pack_detail(
        tags_found: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "complete": True,
            "missing_episodes": [],
            "found_episodes": [],
            "consistent_tags": True,
            "tags_found": tags_found or {},
        }

    @staticmethod
    def _default_pack_season(meta: Meta) -> int:
        try:
            return int(meta.season_int)
        except TypeError, ValueError:
            return 1

    @staticmethod
    def _standard_episode_identities(filename: str) -> list[tuple[int, int]]:
        identities: list[tuple[int, int]] = []
        matches = re.findall(
            r"[Ss](\d{1,2})[Ee](\d{1,3})(?:[Ee](\d{1,3}))?", filename
        )
        for season_str, first_str, second_str in matches:
            season_num = int(season_str)
            identities.append((season_num, int(first_str)))
            if second_str:
                identities.append((season_num, int(second_str)))
        return identities

    @staticmethod
    def _episode_only_identities(
        filename: str, default_season: int
    ) -> list[tuple[int, int]]:
        identities: list[tuple[int, int]] = []
        matches = re.findall(r"\b[Ee](\d{1,3})(?:[Ee](\d{1,3}))?\b", filename)
        for first_str, second_str in matches:
            identities.append((default_season, int(first_str)))
            if second_str:
                identities.append((default_season, int(second_str)))
        return identities

    @staticmethod
    def _anime_episode_identities(
        filename: str, default_season: int
    ) -> list[tuple[int, int]]:
        matches = re.findall(
            r"(?:\s-\s)?(\d{1,4})(?:v\d+)?\s*\((?:\d+[pi])\)", filename
        )
        return [(default_season, int(match)) for match in matches]

    @classmethod
    def _episode_identities(
        cls, filename: str, default_season: int
    ) -> list[tuple[int, int]]:
        identities = cls._standard_episode_identities(filename)
        if identities:
            return identities
        identities = cls._episode_only_identities(filename, default_season)
        if identities:
            return identities
        return cls._anime_episode_identities(filename, default_season)

    @staticmethod
    async def _record_pack_tag(
        tags_found: dict[str, list[str]],
        file_path: str,
        filename: str,
        meta: Meta,
    ) -> None:
        file_tag = await get_tag(file_path, meta, season_pack_check=True)
        if not file_tag:
            return
        tag_clean = file_tag.lstrip("-")
        tags_found.setdefault(tag_clean, []).append(filename)

    @classmethod
    async def _scan_pack_files(
        cls, files: list[str], meta: Meta, default_season: int
    ) -> tuple[list[tuple[int, int]], dict[str, list[str]]]:
        found_episodes: list[tuple[int, int]] = []
        tags_found: dict[str, list[str]] = {}
        for file_path in files:
            filename = Path(file_path).name
            await cls._record_pack_tag(tags_found, file_path, filename, meta)
            found_episodes.extend(
                cls._episode_identities(filename, default_season)
            )
        return found_episodes, tags_found

    @staticmethod
    def _missing_for_season(
        found_episodes: list[tuple[int, int]], season: int
    ) -> list[tuple[int, int]]:
        season_episodes = [
            episode
            for found_season, episode in found_episodes
            if found_season == season
        ]
        minimum = min(season_episodes)
        maximum = max(season_episodes)
        return [
            (season, episode)
            for episode in range(minimum, maximum + 1)
            if episode not in season_episodes
        ]

    @classmethod
    def _missing_pack_episodes(
        cls, found_episodes: list[tuple[int, int]]
    ) -> tuple[list[tuple[int, int]], set[int]]:
        seasons = {season for season, _episode in found_episodes}
        missing: list[tuple[int, int]] = []
        for season in seasons:
            missing.extend(cls._missing_for_season(found_episodes, season))
        return missing, seasons

    @staticmethod
    def _pack_detail_result(
        found_episodes: list[tuple[int, int]],
        missing_episodes: list[tuple[int, int]],
        seasons: set[int],
        tags_found: dict[str, list[str]],
    ) -> dict[str, Any]:
        return {
            "complete": not missing_episodes,
            "missing_episodes": missing_episodes,
            "found_episodes": found_episodes,
            "seasons": list(seasons),
            "consistent_tags": len(tags_found) <= 1,
            "tags_found": tags_found,
        }

    @staticmethod
    def _log_pack_detail(result: Mapping[str, Any]) -> None:
        found_episodes = result["found_episodes"]
        missing_episodes = result["missing_episodes"]
        tags_found = cast(Mapping[str, list[str]], result["tags_found"])
        logger.debug("[cyan]Season pack completeness check:")
        logger.debug(f"[cyan]Found episodes: {found_episodes}")
        if missing_episodes:
            logger.debug(f"[red]Missing episodes: {missing_episodes}")
        else:
            logger.debug("[green]Season pack episode list appears complete")
        if tags_found:
            logger.debug(f"[cyan]Group tags found: {list(tags_found.keys())}")
            if not result["consistent_tags"]:
                logger.debug(
                    "[yellow]Warning: Multiple group tags detected in season pack"
                )

    async def check_season_pack_detail(self, meta: Meta) -> dict[str, Any]:
        if not meta.tv_pack:
            return self._empty_pack_detail()
        files = cast(list[str], meta.filelist)
        if not files:
            return self._empty_pack_detail()
        found_episodes, tags_found = await self._scan_pack_files(
            files, meta, self._default_pack_season(meta)
        )
        if not found_episodes:
            logger.info("[red]No episodes found in the season pack files.")
            return self._empty_pack_detail(tags_found)
        found_episodes = sorted(set(found_episodes))
        missing_episodes, seasons = self._missing_pack_episodes(found_episodes)
        result = self._pack_detail_result(
            found_episodes, missing_episodes, seasons, tags_found
        )
        self._log_pack_detail(result)
        return result
