# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import click
import httpx
from rich.markup import escape

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import artwork_dir
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Cinematik(UNIT3D):
    """
    Cinematik is a Private tracker for full BD and DVD discs of non-mainstream movies, niche cinema and arthouse.
    """

    tracker = "CINEMATIK"
    display_name = "Cinematik"
    allows_bloated_audio = True
    base_url = "https://cinematik.net"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="CINEMATIK")
        self.config: Config = config
        self.uploadscreens_manager = UploadScreensManager(config)

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not meta.is_disc:
            logger.info(f"{self.tracker}: [red]Only disc-based content allowed at Cinematik")
            return False

        return True

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

        return data

    async def get_name(self, meta: Meta) -> dict[str, str]:
        category_id = (await self.get_category_id(meta))["category_id"]
        meta.category_id = category_id
        if category_id in {"1", "3", "5", "6"}:
            return {"name": self._film_disc_name(meta)}
        if meta.category == "TV" and str(meta.type) == "DISC":
            return {"name": self._tv_disc_name(meta)}
        return {"name": ""}

    @classmethod
    def _film_disc_name(cls, meta: Meta) -> str:
        identity = cls._title_identity(meta, str(meta.year) if meta.year is not None else "")
        if meta.is_disc == "BDMV":
            return cls._compact_name(f"{identity} {meta.disctype} {meta.resolution} {meta.video_codec} {cls._three_d_tag(meta)}")
        if meta.is_disc == "DVD":
            return cls._compact_name(f"{identity} {meta.source} {meta.dvd_size}")
        return ""

    @classmethod
    def _tv_disc_name(cls, meta: Meta) -> str:
        year = str(meta.search_year or meta.year or "")
        identity = cls._title_identity(meta, year)
        if meta.is_disc == "BDMV":
            return cls._compact_name(f"{identity} {meta.season} {meta.disctype} {meta.resolution} {meta.video_codec}")
        if meta.is_disc == "DVD":
            return cls._compact_name(f"{identity} {meta.season} {meta.source} {meta.dvd_size}")
        return ""

    @staticmethod
    def _title_identity(meta: Meta, year: str) -> str:
        title = str(meta.title).replace("AKA", "/").strip()
        alt = str(meta.aka).replace("AKA", "/").strip()
        alt_part = f" {alt}" if alt else ""
        return f"{title}{alt_part} ({year})"

    @staticmethod
    def _three_d_tag(meta: Meta) -> str:
        return f"[{meta.three_d}]" if meta.three_d else ""

    @staticmethod
    def _compact_name(value: str) -> str:
        return " ".join(value.split())

    async def get_category_id(self, meta: Meta, category: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        _ = (category, reverse, mapping_only)
        category_name = str(meta.category)
        if category_name == "MOVIE":
            return {"category_id": self._movie_category_id(meta)}
        if category_name == "TV":
            return {"category_id": self._tv_category_id(meta)}
        mapping = {"FILM": "1", "Foreign Film": "3", "Foreign TV": "4", "Opera & Musical": "5", "Asian Film": "6"}
        return {"category_id": mapping.get(category_name, "0")}

    @staticmethod
    def _movie_category_id(meta: Meta) -> str:
        if meta.foreign:
            return "3"
        if meta.opera:
            return "5"
        return "6" if meta.asian else "1"

    @staticmethod
    def _tv_category_id(meta: Meta) -> str:
        if meta.foreign:
            return "4"
        return "5" if meta.opera else "2"

    async def get_type_id(self, meta: Meta, type: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        _ = (type, reverse, mapping_only)
        disctype = meta.disctype
        type_id_map = {"Custom": "1", "BD100": "3", "BD66": "4", "BD50": "5", "BD25": "6", "NTSC DVD9": "7", "NTSC DVD5": "8", "PAL DVD9": "9", "PAL DVD5": "10", "3D": "11"}

        if not disctype:
            logger.info(f"{self.tracker}: [red]You must specify a --disctype")
            # Raise an exception since we can't proceed without disctype
            raise ValueError("disctype is required for Cinematik tracker but was not provided")

        disctype_value = str(cast(Any, disctype[0])) if isinstance(disctype, list) and disctype else str(cast(Any, disctype))
        type_id = type_id_map.get(disctype_value, "1")  # '1' is the default fallback

        return {"type_id": type_id}

    async def get_resolution_id(self, meta: Meta, resolution: str | None = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        _ = (resolution, reverse, mapping_only)
        resolution_id = {
            "Other": "10",
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }.get(meta.resolution, "10")
        return {"resolution_id": resolution_id}

    async def get_description(self, meta: Meta) -> dict[str, str]:
        custom = await self._custom_description(meta)
        if custom is not None:
            return {"description": custom}
        discs = self._disc_entries(meta)
        total_bitrate = self._total_bitrate(discs)
        country_name = self.country_code_to_name(str(meta.region))
        poster_url = await self._poster_url(meta)
        screenshots = self._screenshot_urls(meta)
        description = self._build_description(meta, discs, total_bitrate, country_name, poster_url, screenshots)
        description = await self._maybe_edit_description(meta, description)
        await self._write_description(meta, description)
        return {"description": description}

    async def _custom_description(self, meta: Meta) -> str | None:
        if not meta.description_link and not meta.description_file:
            return None
        description = await DescriptionBuilder(self.tracker, self.config).general_description_generator(meta, mediainfo=False, nfo=False)
        logger.info(f"{self.tracker}: Custom Description Link/File Path: {description}", extra={"markup": False})
        return description

    @staticmethod
    def _disc_entries(meta: Meta) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], meta.discs) if isinstance(meta.discs, list) else []

    @staticmethod
    def _total_bitrate(discs: list[dict[str, Any]]) -> str:
        summary = discs[0].get("summary", "") if discs else ""
        if not summary:
            return "Unknown"
        match = re.search(r"Total Bitrate: ([\d.]+ Mbps)", str(summary))
        return match.group(1) if match else "Unknown"

    async def _poster_url(self, meta: Meta) -> str:
        poster_url = f"https://image.tmdb.org/t/p/original{meta.tmdb_poster_path}"
        poster_path = self._existing_poster_path(meta)
        if poster_path is None:
            poster_path = await self._download_poster(meta, poster_url)
        if poster_path is None or not poster_path.exists():
            logger.info(f"{self.tracker}: [red]Cover file not found, cannot upload.[/red]")
            return poster_url
        return await self._rehost_poster(meta, poster_path, poster_url)

    @staticmethod
    def _poster_candidates(meta: Meta) -> list[Path]:
        root = artwork_dir(meta.base_dir, meta.uuid)
        return [root / filename for filename in ("POSTER.png", "poster.png", "POSTER.jpg", "poster.jpg")]

    def _existing_poster_path(self, meta: Meta) -> Path | None:
        existing = next((path for path in self._poster_candidates(meta) if path.is_file()), None)
        if existing is not None:
            logger.info(f"{self.tracker}: [green]Cover already exists as {existing.name}, skipping download.[/green]")
        return existing

    async def _download_poster(self, meta: Meta, poster_url: str) -> Path | None:
        target = artwork_dir(meta.base_dir, meta.uuid) / "poster.jpg"
        try:
            response = await self._poster_response(poster_url)
            target.write_bytes(response.content)
            logger.info(f"{self.tracker}: [green]Cover downloaded to {escape(str(target))}[/green]")
            return target
        except (httpx.HTTPError, OSError, ValueError) as error:
            logger.error(f"{self.tracker}: [red]Error downloading poster: {escape(str(error))}[/red]")
            return None

    @staticmethod
    async def _poster_response(url: str) -> httpx.Response:
        Cinematik._validate_poster_url(url)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response

    @staticmethod
    def _validate_poster_url(url: str) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("Poster URL must use HTTP(S)")

    async def _rehost_poster(self, meta: Meta, poster_path: Path, fallback_url: str) -> str:
        try:
            logger.info(f"{self.tracker}: Uploading standard poster to image host....")
            images, _ = await self.uploadscreens_manager.upload_screens(meta, 1, 1, 0, 1, [str(poster_path)], {})
            return self._first_raw_url(images, fallback_url)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.error(f"{self.tracker}: [red]Error uploading poster: {escape(str(error))}[/red]")
            return fallback_url

    @staticmethod
    def _first_raw_url(images: Any, fallback: str) -> str:
        if not isinstance(images, list) or not images:
            return fallback
        first = images[0]
        if not isinstance(first, dict):
            return fallback
        return str(first.get("raw_url", fallback))

    @staticmethod
    def _screenshot_urls(meta: Meta) -> list[str]:
        images = meta.image_list if isinstance(meta.image_list, list) else []
        urls = [str(item.get("raw_url", "")) for item in images[:6] if isinstance(item, dict)]
        return (urls + [""] * 6)[:6]

    def _build_description(
        self,
        meta: Meta,
        discs: list[dict[str, Any]],
        total_bitrate: str,
        country_name: str,
        poster_url: str,
        screenshots: list[str],
    ) -> str:
        sections = [
            self._cover_section(poster_url),
            self._screens_section(screenshots),
            self._synopsis_section(meta),
            self._technical_section(meta, discs, total_bitrate, country_name),
            self._extras_section(),
            self._comments_section(meta),
        ]
        return "".join(sections)

    @staticmethod
    def _cover_section(poster_url: str) -> str:
        return (
            "[h3]Cover[/h3] [color=red]A stock poster has been automatically added, but you'll get more love if you include a proper cover, see rule 6.6[/color]\n"
            "[center]\n"
            f"[IMG=500]{poster_url}[/IMG]\n"
            "[/center]\n\n"
        )

    @staticmethod
    def _screens_section(urls: list[str]) -> str:
        first, second, third, fourth, fifth, sixth = urls
        return (
            "[h3]Screenshots[/h3]\n[center]\n"
            f"[URL={first}][IMG=300]{first}[/IMG][/URL] "
            f"[URL={second}][IMG=300]{second}[/IMG][/URL] "
            f"[URL={third}][IMG=300]{third}[/IMG][/URL]\n "
            f"[URL={fourth}][IMG=300]{fourth}[/IMG][/URL] "
            f"[URL={fifth}][IMG=300]{fifth}[/IMG][/URL] "
            f"[URL={sixth}][IMG=300]{sixth}[/IMG][/URL]\n[/center]\n\n"
        )

    @staticmethod
    def _synopsis_section(meta: Meta) -> str:
        overview = meta.overview if meta.overview is not None else "No synopsis available."
        return (
            "[h3]Synopsis/Review/Personal Thoughts (edit as needed)[/h3]\n"
            "[color=red]Default TMDB sypnosis added, more love if you use a sypnosis from credible film institutions such as the BFI or directly quoting well-known film critics, see rule 6.3[/color]\n"
            f"[quote]\n{overview}\n[/quote]\n\n"
        )

    def _technical_section(self, meta: Meta, discs: list[dict[str, Any]], total_bitrate: str, country_name: str) -> str:
        lines = ["[h3]Technical Info[/h3]\n", "[code]\n"]
        self._append_technical_header(lines, meta, country_name)
        self._append_audio_subtitles(lines, meta, discs)
        self._append_video_source(lines, meta)
        lines.append(f"  Average Bitrate....: {total_bitrate}\n")
        lines.append("  Ripping Program....:  [color=red]Specify - if it's your rip or custom version, otherwise 'Not my rip'[/color]\n\n")
        self._append_untouched_status(lines, meta)
        lines.append("[/code]\n\n")
        return "".join(lines)

    def _append_technical_header(self, lines: list[str], meta: Meta, country_name: str) -> None:
        bdinfo = meta.bdinfo if isinstance(meta.bdinfo, dict) else {}
        if meta.is_disc == "BDMV":
            lines.append(f"  Disc Label.........:{bdinfo.get('label', '')}\n")
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        lines.append(f"  IMDb...............: [url]{imdb.get('imdb_url', '')!s}{meta.imdb_rating}[/url]\n")
        lines.append(f"  Year...............: {'' if meta.year is None else meta.year}\n")
        lines.append(f"  Country............: {country_name}\n")
        self._append_runtime(lines, meta, bdinfo)

    @staticmethod
    def _append_runtime(lines: list[str], meta: Meta, bdinfo: dict[str, Any]) -> None:
        if meta.is_disc == "BDMV":
            lines.append(f"  Runtime............: {bdinfo.get('length', '')} hrs [color=red](double check this is actual runtime)[/color]\n")
        else:
            lines.append("  Runtime............:  [color=red]Insert the actual runtime[/color]\n")

    def _append_audio_subtitles(self, lines: list[str], meta: Meta, discs: list[dict[str, Any]]) -> None:
        if meta.is_disc == "BDMV":
            self._append_bdmv_audio_subtitles(lines, meta)
            return
        for disc in discs:
            self._append_dvd_audio_subtitles(lines, disc)

    @classmethod
    def _append_bdmv_audio_subtitles(cls, lines: list[str], meta: Meta) -> None:
        bdinfo = cls._bdinfo_mapping(meta)
        lines.append(f"  Audio..............: {cls._bdmv_audio_text(bdinfo)}\n")
        lines.append(f"  Subtitles..........: {cls._bdmv_subtitle_text(bdinfo)}\n")

    @staticmethod
    def _bdinfo_mapping(meta: Meta) -> dict[str, Any]:
        return meta.bdinfo if isinstance(meta.bdinfo, dict) else {}

    @staticmethod
    def _bdmv_audio_text(bdinfo: dict[str, Any]) -> str:
        audio = bdinfo.get("audio", [])
        tracks = audio if isinstance(audio, list) else []
        return ", ".join(Cinematik._bdmv_audio_track_text(track) for track in tracks if isinstance(track, dict))

    @staticmethod
    def _bdmv_audio_track_text(track: dict[str, Any]) -> str:
        return f"{track.get('language', 'Unknown')} {track.get('codec', 'Unknown')} {track.get('channels', 'Unknown')}"

    @staticmethod
    def _bdmv_subtitle_text(bdinfo: dict[str, Any]) -> str:
        subtitles = bdinfo.get("subtitles", [])
        values = subtitles if isinstance(subtitles, list) else []
        return ", ".join(str(value) for value in values)

    def _append_dvd_audio_subtitles(self, lines: list[str], disc: dict[str, Any]) -> None:
        audio = self._dvd_audio_info(disc)
        if audio:
            lines.append(f"  Audio..............: {audio}\n")
        subtitles = self.parse_subtitles(str(disc.get("ifo_mi", "")))
        if subtitles:
            lines.append(f"  Subtitles..........: {', '.join(sorted(subtitles))}\n")

    def _dvd_audio_info(self, disc: dict[str, Any]) -> str:
        section = self._dvd_audio_section(str(disc.get("vob_mi", "")))
        if not section:
            return ""
        codec = self._dvd_audio_codec(section)
        channels = self._dvd_audio_channels(section)
        language = self._dvd_audio_language(str(disc.get("ifo_mi_full", "")))
        return f"{language} {codec} {channels}"

    @staticmethod
    def _dvd_audio_section(value: str) -> str:
        if "Audio\n" not in value:
            return ""
        return value.split("\n\nAudio\n", 1)[1].split("\n\n", 1)[0]

    @staticmethod
    def _dvd_audio_codec(section: str) -> str:
        mapping = (("AC-3", "AC-3"), ("DTS", "DTS"), ("MPEG Audio", "MPEG Audio"), ("PCM", "PCM"), ("AAC", "AAC"))
        return next((value for key, value in mapping if key in section), "Unknown")

    @staticmethod
    def _dvd_audio_channels(section: str) -> str:
        if "Channel(s)" not in section:
            return "Unknown"
        value = section.split("Channel(s)", 1)[1].split(":", 1)[1].strip().split(" ", 1)[0]
        return "5.1" if value == "6" else value

    @staticmethod
    def _dvd_audio_language(value: str) -> str:
        if "Language" not in value:
            return "Unknown"
        return value.split("Language", 1)[1].split(":", 1)[1].strip().split("\n", 1)[0]

    @classmethod
    def _append_video_source(cls, lines: list[str], meta: Meta) -> None:
        lines.append(cls._video_format_line(meta))
        lines.append("  Film Aspect Ratio..: [color=red]The actual aspect ratio of the content, not including the black bars[/color]\n")
        lines.append(cls._technical_source_line(meta))
        lines.append(cls._distributor_line(meta))

    @classmethod
    def _video_format_line(cls, meta: Meta) -> str:
        if meta.is_disc != "BDMV":
            return cls._dvd_format_line(meta)
        return f"  Video Format.......: {cls._bdmv_video_resolution(meta)}\n"

    @staticmethod
    def _dvd_format_line(meta: Meta) -> str:
        source = meta.source if meta.source is not None else "Unknown"
        return f"  DVD Format.........: {source}\n"

    @classmethod
    def _bdmv_video_resolution(cls, meta: Meta) -> str:
        video = cls._bdinfo_mapping(meta).get("video", [])
        tracks = video if isinstance(video, list) else []
        if not tracks or not isinstance(tracks[0], dict):
            return "Unknown"
        return str(tracks[0].get("resolution", "Unknown"))

    @staticmethod
    def _technical_source_line(meta: Meta) -> str:
        source = meta.disctype if meta.is_disc == "BDMV" else meta.dvd_size
        value = source if source is not None else "Unknown"
        return f"  Source.............: {value}\n"

    @staticmethod
    def _distributor_line(meta: Meta) -> str:
        distributor = meta.distributor if meta.distributor is not None else "Unknown"
        return f"  Film Distributor...: [url={meta.distributor_link}]{distributor}[/url] [color=red]Don't forget the actual distributor link\n"

    @staticmethod
    def _append_untouched_status(lines: list[str], meta: Meta) -> None:
        if meta.untouched is True:
            lines.extend(
                [
                    "  Menus......: [X] Untouched\n",
                    "  Video......: [X] Untouched\n",
                    "  Extras.....: [X] Untouched\n",
                    "  Audio......: [X] Untouched\n",
                ]
            )
            return
        lines.extend(
            [
                "  Menus......: [ ] Untouched\n",
                "               [ ] Stripped\n",
                "  Video......: [ ] Untouched\n",
                "               [ ] Re-encoded\n",
                "  Extras.....: [ ] Untouched\n",
                "               [ ] Stripped\n",
                "               [ ] Re-encoded\n",
                "               [ ] None\n",
                "  Audio......: [ ] Untouched\n",
                "               [ ] Stripped tracks\n",
            ]
        )

    @staticmethod
    def _extras_section() -> str:
        return "[h4]Extras[/h4]\n[*] Insert special feature 1 here\n[*] Insert special feature 2 here\n... (add more special features as needed)\n\n"

    @staticmethod
    def _comments_section(meta: Meta) -> str:
        comments = meta.uploader_comments if meta.uploader_comments is not None else "No comments."
        return f"[h4]Uploader Comments[/h4]\n - {comments}\n"

    async def _maybe_edit_description(self, meta: Meta, description: str) -> str:
        if meta.unattended and not meta.unattended_confirm:
            logger.info(f"{self.tracker}: [green]Unattended mode: Keeping the original description.[/green]")
            return description
        return self._interactive_description(description)

    def _interactive_description(self, description: str) -> str:
        logger.info(f"{self.tracker}: Current description: {description}", extra={"markup": False})
        logger.info(f"{self.tracker}: [cyan]Do you want to edit or keep the description?[/cyan]")
        choice = cli_ui.ask_string("Enter 'e' to edit, or press Enter to keep it as is: ")
        if (choice or "").lower() != "e":
            logger.info(f"{self.tracker}: [green]Keeping the original description.[/green]")
            return description
        return self._edited_description(description)

    def _edited_description(self, description: str) -> str:
        edited = cast(str | None, click.edit(description))
        result = edited.strip() if edited else description
        logger.info(f"{self.tracker}: Final description after editing: {result}", extra={"markup": False})
        return result

    async def _write_description(self, meta: Meta, description: str) -> None:
        path = Path(meta.base_dir) / "tmp" / meta.uuid / f"[{self.tracker}]DESCRIPTION.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(description)

    def parse_subtitles(self, disc_mi: str) -> set[str]:
        unique_subtitles: set[str] = set()  # Store unique subtitle strings
        lines = disc_mi.splitlines()  # Split the multiline text into individual lines
        current_block = None

        for line in lines:
            # Detect the start of a subtitle block (Text #)
            if line.startswith("Text #"):
                current_block = "subtitle"
                continue

            # Extract language information for subtitles
            if current_block == "subtitle" and "Language" in line:
                language = line.split(":")[1].strip()
                unique_subtitles.add(language)

        return unique_subtitles

    def country_code_to_name(self, code: str) -> str:
        country_mapping = {
            "AFG": "Afghanistan",
            "ALB": "Albania",
            "DZA": "Algeria",
            "AND": "Andorra",
            "AGO": "Angola",
            "ARG": "Argentina",
            "ARM": "Armenia",
            "AUS": "Australia",
            "AUT": "Austria",
            "AZE": "Azerbaijan",
            "BHS": "Bahamas",
            "BHR": "Bahrain",
            "BGD": "Bangladesh",
            "BRB": "Barbados",
            "BLR": "Belarus",
            "BEL": "Belgium",
            "BLZ": "Belize",
            "BEN": "Benin",
            "BTN": "Bhutan",
            "BOL": "Bolivia",
            "BIH": "Bosnia and Herzegovina",
            "BWA": "Botswana",
            "BRA": "Brazil",
            "BRN": "Brunei",
            "BGR": "Bulgaria",
            "BFA": "Burkina Faso",
            "BDI": "Burundi",
            "CPV": "Cabo Verde",
            "KHM": "Cambodia",
            "CMR": "Cameroon",
            "CAN": "Canada",
            "CAF": "Central African Republic",
            "TCD": "Chad",
            "CHL": "Chile",
            "CHN": "China",
            "COL": "Colombia",
            "COM": "Comoros",
            "COG": "Congo",
            "CRI": "Costa Rica",
            "HRV": "Croatia",
            "CUB": "Cuba",
            "CYP": "Cyprus",
            "CZE": "Czech Republic",
            "DNK": "Denmark",
            "DJI": "Djibouti",
            "DMA": "Dominica",
            "DOM": "Dominican Republic",
            "ECU": "Ecuador",
            "EGY": "Egypt",
            "SLV": "El Salvador",
            "GNQ": "Equatorial Guinea",
            "ERI": "Eritrea",
            "EST": "Estonia",
            "SWZ": "Eswatini",
            "ETH": "Ethiopia",
            "FJI": "Fiji",
            "FIN": "Finland",
            "FRA": "France",
            "GAB": "Gabon",
            "GMB": "Gambia",
            "GEO": "Georgia",
            "DEU": "Germany",
            "GHA": "Ghana",
            "GRC": "Greece",
            "GRD": "Grenada",
            "GTM": "Guatemala",
            "GIN": "Guinea",
            "GNB": "Guinea-Bissau",
            "GUY": "Guyana",
            "HTI": "Haiti",
            "HND": "Honduras",
            "HUN": "Hungary",
            "ISL": "Iceland",
            "IND": "India",
            "IDN": "Indonesia",
            "IRN": "Iran",
            "IRQ": "Iraq",
            "IRL": "Ireland",
            "ISR": "Israel",
            "ITA": "Italy",
            "JAM": "Jamaica",
            "JPN": "Japan",
            "JOR": "Jordan",
            "KAZ": "Kazakhstan",
            "KEN": "Kenya",
            "KIR": "Kiribati",
            "KOR": "Korea",
            "KWT": "Kuwait",
            "KGZ": "Kyrgyzstan",
            "LAO": "Laos",
            "LVA": "Latvia",
            "LBN": "Lebanon",
            "LSO": "Lesotho",
            "LBR": "Liberia",
            "LBY": "Libya",
            "LIE": "Liechtenstein",
            "LTU": "Lithuania",
            "LUX": "Luxembourg",
            "MDG": "Madagascar",
            "MWI": "Malawi",
            "MYS": "Malaysia",
            "MDV": "Maldives",
            "MLI": "Mali",
            "MLT": "Malta",
            "MHL": "Marshall Islands",
            "MRT": "Mauritania",
            "MUS": "Mauritius",
            "MEX": "Mexico",
            "FSM": "Micronesia",
            "MDA": "Moldova",
            "MCO": "Monaco",
            "MNG": "Mongolia",
            "MNE": "Montenegro",
            "MAR": "Morocco",
            "MOZ": "Mozambique",
            "MMR": "Myanmar",
            "NAM": "Namibia",
            "NRU": "Nauru",
            "NPL": "Nepal",
            "NLD": "Netherlands",
            "NZL": "New Zealand",
            "NIC": "Nicaragua",
            "NER": "Niger",
            "NGA": "Nigeria",
            "MKD": "North Macedonia",
            "NOR": "Norway",
            "OMN": "Oman",
            "PAK": "Pakistan",
            "PLW": "Palau",
            "PAN": "Panama",
            "PNG": "Papua New Guinea",
            "PRY": "Paraguay",
            "PER": "Peru",
            "PHL": "Philippines",
            "POL": "Poland",
            "PRT": "Portugal",
            "QAT": "Qatar",
            "ROU": "Romania",
            "RUS": "Russia",
            "RWA": "Rwanda",
            "KNA": "Saint Kitts and Nevis",
            "LCA": "Saint Lucia",
            "VCT": "Saint Vincent and the Grenadines",
            "WSM": "Samoa",
            "SMR": "San Marino",
            "STP": "Sao Tome and Principe",
            "SAU": "Saudi Arabia",
            "SEN": "Senegal",
            "SRB": "Serbia",
            "SYC": "Seychelles",
            "SLE": "Sierra Leone",
            "SGP": "Singapore",
            "SVK": "Slovakia",
            "SVN": "Slovenia",
            "SLB": "Solomon Islands",
            "SOM": "Somalia",
            "ZAF": "South Africa",
            "SSD": "South Sudan",
            "ESP": "Spain",
            "LKA": "Sri Lanka",
            "SDN": "Sudan",
            "SUR": "Suriname",
            "SWE": "Sweden",
            "CHE": "Switzerland",
            "SYR": "Syria",
            "TWN": "Taiwan",
            "TJK": "Tajikistan",
            "TZA": "Tanzania",
            "THA": "Thailand",
            "TLS": "Timor-Leste",
            "TGO": "Togo",
            "TON": "Tonga",
            "TTO": "Trinidad and Tobago",
            "TUN": "Tunisia",
            "TUR": "Turkey",
            "TKM": "Turkmenistan",
            "TUV": "Tuvalu",
            "UGA": "Uganda",
            "UKR": "Ukraine",
            "ARE": "United Arab Emirates",
            "GBR": "United Kingdom",
            "USA": "United States",
            "URY": "Uruguay",
            "UZB": "Uzbekistan",
            "VUT": "Vanuatu",
            "VEN": "Venezuela",
            "VNM": "Vietnam",
            "YEM": "Yemen",
            "ZMB": "Zambia",
            "ZWE": "Zimbabwe",
        }
        return country_mapping.get(code.upper(), "Unknown Country")
