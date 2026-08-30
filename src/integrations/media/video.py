# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import json
import os
import re
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui

from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.media.media_info_export import mi_resolution
from src.integrations.observability.runtime_support import logger


class VideoManager:
    @staticmethod
    def _guess_is_uhd(guess: Any, path: str) -> bool:
        guess_dict = cast(dict[str, Any], guess)
        source = str(guess_dict.get("Source", ""))
        other = str(guess_dict.get("Other", ""))
        return (
            source == "Ultra HD Blu-ray"
            or (source == "Blu-ray" and other == "Ultra HD")
            or "UHD" in path
        )

    async def get_uhd(
        self, type: str, guess: Any, resolution: str, path: str
    ) -> str:
        if self._guess_is_uhd(guess, path):
            return "UHD"
        if type in ("DISC", "REMUX", "ENCODE") and resolution == "2160p":
            return "UHD"
        return ""

    @staticmethod
    def _disc_track_hdr(track: dict[str, Any]) -> tuple[str, str]:
        value = str(track.get("hdr_dv", ""))
        if "HDR10+" in value:
            hdr = "HDR10+"
        elif "HDR10" in value:
            hdr = "HDR"
        else:
            hdr = ""
        return ("DV" if "Dolby Vision" in value else ""), hdr

    @staticmethod
    def _merged_disc_hdr(
        current_dv: str, current_hdr: str, track_dv: str, track_hdr: str
    ) -> tuple[str, str]:
        dv = track_dv or current_dv
        if track_hdr == "HDR10+":
            return dv, track_hdr
        if track_hdr and current_hdr != "HDR10+":
            return dv, track_hdr
        return dv, current_hdr

    @classmethod
    def _disc_hdr(cls, bdinfo: Any) -> str:
        bdinfo_dict = cast(dict[str, Any], bdinfo)
        dv = ""
        hdr = ""
        for raw_track in bdinfo_dict.get("video", []):
            if not isinstance(raw_track, dict):
                continue
            track_dv, track_hdr = cls._disc_track_hdr(
                cast(dict[str, Any], raw_track)
            )
            dv, hdr = cls._merged_disc_hdr(dv, hdr, track_dv, track_hdr)
        return f"{dv} {hdr}".strip()

    @staticmethod
    def _mediainfo_video_track(mi: Any) -> dict[str, Any] | None:
        mi_dict = cast(dict[str, Any], mi)
        tracks = mi_dict.get("media", {}).get("track", [])
        for raw_track in tracks:
            if not isinstance(raw_track, dict):
                continue
            track = cast(dict[str, Any], raw_track)
            if str(track.get("@type", "")).casefold() == "video":
                return track
        return None

    @staticmethod
    def _hdr_format_string(track: dict[str, Any]) -> str:
        for key in (
            "HDR_Format_Compatibility",
            "HDR_Format_String",
            "HDR_Format",
        ):
            value = track.get(key, "")
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _base_hdr_from_format_string(value: str) -> str:
        if "HDR10+" in value:
            return "HDR10+"
        if "HDR10" in value or "SMPTE ST 2094 App 4" in value:
            return "HDR"
        return ""

    @classmethod
    def _hdr_from_format_string(cls, value: str) -> str:
        hdr = cls._base_hdr_from_format_string(value)
        return f"{hdr} HLG".strip() if "HLG" in value else hdr

    @staticmethod
    def _pq_hdr(track: dict[str, Any], format_string: str) -> str:
        if format_string:
            return ""
        transfer_values = (
            track.get("transfer_characteristics"),
            track.get("transfer_characteristics_Original"),
        )
        return "PQ10" if "PQ" in transfer_values else ""

    @staticmethod
    def _original_transfer_hdr(track: dict[str, Any], current: str) -> str:
        transfer = str(track.get("transfer_characteristics_Original") or "")
        if "HLG" in transfer:
            return "HLG"
        if current != "HLG" and "BT.2020 (10-bit)" in transfer:
            return "WCG"
        return current

    @classmethod
    def _file_hdr(cls, track: dict[str, Any]) -> str:
        if track.get("colour_primaries") not in ("BT.2020", "REC.2020"):
            return ""
        format_string = cls._hdr_format_string(track)
        hdr = cls._hdr_from_format_string(format_string)
        hdr = hdr or cls._pq_hdr(track, format_string)
        return cls._original_transfer_hdr(track, hdr)

    @staticmethod
    def _file_dv(track: dict[str, Any]) -> str:
        formats = (
            str(track.get("HDR_Format", "")),
            str(track.get("HDR_Format_String", "")),
        )
        return (
            "DV" if any("Dolby Vision" in value for value in formats) else ""
        )

    async def get_hdr(self, mi: Any, bdinfo: Any | None) -> str:
        if bdinfo:
            return self._disc_hdr(bdinfo)
        track = self._mediainfo_video_track(mi)
        if track is None:
            return ""
        return f"{self._file_dv(track)} {self._file_hdr(track)}".strip()

    async def get_video_codec(self, bdinfo: Any) -> str:
        codecs = {
            "MPEG-2 Video": "MPEG-2",
            "MPEG-4 AVC Video": "AVC",
            "MPEG-H HEVC Video": "HEVC",
            "VC-1 Video": "VC-1",
        }
        bdinfo_dict = cast(dict[str, Any], bdinfo)
        return codecs.get(bdinfo_dict["video"][0]["codec"], "")

    @staticmethod
    def _video_encode_fields(
        mi: Any, bdinfo: Any
    ) -> tuple[str, str, bool, str, str]:
        try:
            mi_dict = cast(dict[str, Any], mi)
            track = cast(dict[str, Any], mi_dict["media"]["track"][1])
            format_name = str(track["Format"])
            profile = str(track.get("Format_Profile", format_name))
            has_settings = bool(track.get("Encoded_Library_Settings"))
            bit_depth = str(track.get("BitDepth", "0"))
            library = str(track.get("Encoded_Library_Name") or "")
            return format_name, profile, has_settings, bit_depth, library
        except Exception:
            bdinfo_dict = cast(dict[str, Any], bdinfo)
            track = cast(dict[str, Any], bdinfo_dict["video"][0])
            return (
                str(track["codec"]),
                str(track["profile"]),
                False,
                "0",
                "",
            )

    @staticmethod
    def _legacy_visual_codec(format_name: str, library: str) -> str:
        if format_name != "MPEG-4 Visual" or not library:
            return ""
        library_lower = library.lower()
        if "xvid" in library_lower:
            return "XviD"
        if "divx" in library_lower:
            return "DivX"
        return ""

    @classmethod
    def _encode_codec(cls, format_name: str, library: str) -> str:
        codecs = {"AVC": "x264", "HEVC": "x265"}
        return codecs.get(format_name) or cls._legacy_visual_codec(
            format_name, library
        )

    @staticmethod
    def _web_codec(
        format_name: str, type_name: str, has_encode_settings: bool
    ) -> str:
        codec = {"AVC": "H.264", "HEVC": "H.265"}.get(format_name, "")
        if type_name == "HDTV" and has_encode_settings:
            return codec.replace("H.", "x")
        return codec

    @classmethod
    def _release_codec(
        cls,
        format_name: str,
        type_name: str,
        library: str,
        has_encode_settings: bool,
    ) -> str:
        if format_name in ("AV1", "VP9", "VC-1"):
            return format_name
        if type_name in ("ENCODE", "WEBRIP", "DVDRIP"):
            return cls._encode_codec(format_name, library)
        if type_name in ("WEBDL", "HDTV"):
            return cls._web_codec(format_name, type_name, has_encode_settings)
        return ""

    @staticmethod
    def _video_codec_name(format_name: str, mi: Any) -> str:
        if format_name != "MPEG Video":
            return format_name
        mi_dict = cast(dict[str, Any], mi)
        track = cast(dict[str, Any], mi_dict["media"]["track"][1])
        return f"MPEG-{track.get('Format_Version')}"

    async def get_video_encode(
        self, mi: Any, type: str, bdinfo: Any
    ) -> tuple[str, str, bool, str]:
        format_name, format_profile, has_settings, bit_depth, library = (
            self._video_encode_fields(mi, bdinfo)
        )
        codec = self._release_codec(format_name, type, library, has_settings)
        profile = "Hi10P" if format_profile == "High 10" else ""
        video_encode = " ".join(filter(None, (profile, codec)))
        return (
            video_encode,
            self._video_codec_name(format_name, mi),
            has_settings,
            bit_depth,
        )

    @staticmethod
    def _directory_entries(videoloc: str) -> list[str]:
        try:
            return [p.name for p in Path(videoloc).iterdir() if p.is_file()]
        except Exception:
            return []

    @staticmethod
    def _is_video_entry(filename: str) -> bool:
        extension = Path(filename).suffix.lower()
        if extension not in {".mkv", ".mp4", ".ts", ".avi"}:
            return False
        lower = filename.lower()
        return "sample" not in lower or "!sample" in lower

    @classmethod
    def _directory_filelist(
        cls, videoloc: str, entries: list[str]
    ) -> list[str]:
        return sorted(
            str((Path(videoloc) / filename).resolve())
            for filename in entries
            if cls._is_video_entry(filename)
        )

    @staticmethod
    def _has_arr_tag(filename: str) -> bool:
        return any(tag in filename for tag in ("{tmdb-", "{imdb-", "{tvdb-"))

    @staticmethod
    async def _reject_arr_filename(videoloc: str, message: str) -> None:
        logger.info("[red]Exiting on user request[/red]")
        await cleanup_manager.cleanup()
        cleanup_manager.reset_terminal()
        raise ItemProcessingError(message, videoloc)

    @classmethod
    async def _validate_arr_filename(
        cls, filename: str, videoloc: str
    ) -> None:
        if not cls._has_arr_tag(filename):
            return
        logger.info(
            f"[bold red]This looks like some *arr renamed file which is not allowed: [yellow]{filename}"
        )
        try:
            accepted = cli_ui.ask_yes_no(
                "Do you want to upload with this file?", default=False
            )
        except EOFError:
            logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
            await cls._reject_arr_filename(
                videoloc, "User cancelled filename check prompt."
            )
            return
        if not accepted:
            await cls._reject_arr_filename(
                videoloc, "ARR renamed file rejected by user input."
            )

    @staticmethod
    def _no_video_reason(entries: list[str]) -> str:
        archive_only = any(
            re.search(r"\.(?:rar|r\d{2,3})$", entry, re.I) for entry in entries
        )
        if archive_only:
            return "Video exists only inside an archive; archive-only video uploads are unsupported"
        return "No Video files found"

    @staticmethod
    def _sorted_video_files(filelist: list[str], by_size: bool) -> list[str]:
        if by_size:
            return sorted(filelist, key=os.path.getsize, reverse=True)
        return sorted(filelist)

    @classmethod
    async def _directory_video(
        cls, videoloc: str, mode: str, sorted_filelist: bool
    ) -> tuple[str, list[str]]:
        logger.debug("[blue]Scanning directory for video files...[/blue]")
        entries = cls._directory_entries(videoloc)
        filelist = cls._directory_filelist(videoloc, entries)
        if filelist:
            logger.debug(
                f"[blue]Found {len(filelist)} video files in directory.[/blue]"
            )
        for filename in filelist:
            await cls._validate_arr_filename(filename, videoloc)
        if not filelist:
            reason = cls._no_video_reason(entries)
            logger.info(f"[bold red]{reason}")
            if mode == "cli":
                raise ItemProcessingError(reason, videoloc) from None
            return "", []
        sorted_files = cls._sorted_video_files(filelist, sorted_filelist)
        return sorted_files[0], sorted_files

    async def get_video(
        self, videoloc: str, mode: str, sorted_filelist: bool = False
    ) -> tuple[str, list[str]]:
        resolved = str(Path(videoloc).resolve())
        logger.debug(
            f"[blue]Video location: [yellow]{resolved}[/yellow][/blue]"
        )
        if Path(resolved).is_dir():
            return await self._directory_video(resolved, mode, sorted_filelist)
        await self._validate_arr_filename(resolved, resolved)
        filelist = self._sorted_video_files([resolved], sorted_filelist)
        return resolved, filelist

    @staticmethod
    def _first_disc(meta: Meta) -> dict[str, Any] | None:
        discs = meta.discs
        if not isinstance(discs, list) or not discs:
            return None
        first = discs[0]
        if not isinstance(first, dict):
            return None
        return cast(dict[str, Any], first)

    @staticmethod
    def _decoded_media_info(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
        if not isinstance(value, str):
            return {}
        try:
            loaded = json.loads(value)
        except Exception:
            return {}
        return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}

    @classmethod
    def _dvd_resolution_source(cls, meta: Meta) -> tuple[dict[str, Any], str]:
        disc = cls._first_disc(meta)
        if disc is None:
            return {}, ""
        media_info = cls._decoded_media_info(disc.get("ifo_mi_json", {}))
        text = str(disc.get("vob_mi", "") or disc.get("ifo_mi", ""))
        return media_info, text

    @classmethod
    async def _resolution_media_info(
        cls, meta: Meta, folder_id: str, base_dir: str
    ) -> tuple[dict[str, Any], str]:
        if meta.is_disc == "DVD":
            return cls._dvd_resolution_source(meta)
        path = Path(base_dir) / "tmp" / folder_id / "MediaInfo.json"
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return cast(dict[str, Any], json.loads(await handle.read())), ""

    @staticmethod
    def _resolution_video_track(mi: dict[str, Any]) -> dict[str, Any]:
        tracks = mi.get("media", {}).get("track", [])
        if not isinstance(tracks, list) or len(tracks) <= 1:
            return {}
        typed_tracks = cast(list[Any], tracks)
        track = typed_tracks[1]
        return cast(dict[str, Any], track) if isinstance(track, dict) else {}

    @staticmethod
    def _dimension(value: Any) -> int:
        try:
            return int(float(value))
        except Exception:
            return 0

    @staticmethod
    def _text_dimensions(text: str) -> tuple[int, int]:
        width_match = re.search(r"Width\s*:\s*(\d+)", text, re.IGNORECASE)
        height_match = re.search(r"Height\s*:\s*(\d+)", text, re.IGNORECASE)
        if width_match is None or height_match is None:
            return 0, 0
        return int(width_match.group(1)), int(height_match.group(1))

    @classmethod
    def _dimensions(
        cls, track: dict[str, Any], dvd_text: str
    ) -> tuple[int, int]:
        width = cls._dimension(track.get("Width", 0))
        height = cls._dimension(track.get("Height", 0))
        if width and height:
            return width, height
        if dvd_text:
            return cls._text_dimensions(dvd_text)
        return width, height

    @staticmethod
    def _track_frame_rate(track: dict[str, Any]) -> Any:
        for key in ("FrameRate", "FrameRate_Original", "FrameRate_Num"):
            value = track.get(key)
            if value and value != "0":
                return value
        return None

    @staticmethod
    def _text_frame_rate(text: str) -> str | None:
        match = re.search(r"Frame rate\s*:\s*([\d.]+)", text, re.IGNORECASE)
        return match.group(1) if match else None

    @classmethod
    def _frame_rate(cls, track: dict[str, Any], dvd_text: str) -> Any:
        value = cls._track_frame_rate(track)
        if value is not None:
            return value
        return cls._text_frame_rate(dvd_text) or "24.000"

    @staticmethod
    def _is_hfr(frame_rate: Any) -> bool:
        try:
            return int(float(frame_rate)) > 30
        except Exception:
            return False

    @staticmethod
    def _text_scan_type(text: str) -> str:
        match = re.search(r"Scan type\s*:\s*([^\r\n]+)", text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @classmethod
    def _scan_type(
        cls, track: dict[str, Any], dvd_text: str, folder_id: str
    ) -> str:
        scan = str(track.get("ScanType", "")) or cls._text_scan_type(dvd_text)
        if scan == "Progressive":
            return "p"
        if scan == "Interlaced":
            return "i"
        return (
            "i"
            if re.search(r"\b(?:1080i|576i|480i)\b", folder_id, re.I)
            else "p"
        )

    async def get_resolution(
        self, guess: Any, folder_id: str, base_dir: str, meta: Meta
    ) -> tuple[str, bool]:
        mi, dvd_text = await self._resolution_media_info(
            meta, folder_id, base_dir
        )
        track = self._resolution_video_track(mi)
        width, height = self._dimensions(track, dvd_text)
        frame_rate = self._frame_rate(track, dvd_text)
        scan = self._scan_type(track, dvd_text, folder_id)
        width = self.closest(
            [3840, 2560, 1920, 1280, 1024, 854, 720, 15360, 7680, 0], width
        )
        height = self.closest(
            [2160, 1440, 1080, 720, 576, 540, 480, 8640, 4320, 0], height
        )
        resolution = await mi_resolution(
            f"{width}x{height}{scan}", guess, width, scan
        )
        return resolution, self._is_hfr(frame_rate)

    def closest(self, lst: list[int], k: int) -> int:
        # Get closest, but not over
        lst = sorted(lst)
        mi_input = k
        res = 0
        for each in lst:
            if mi_input > each:
                pass
            else:
                res = each
                break
        return res

    @staticmethod
    def _matched_release_type(filename: str) -> str:
        rules = (
            (("remux",), "REMUX"),
            ((" web ", ".web.", "web-dl", "webdl"), "WEBDL"),
            (("webrip",), "WEBRIP"),
            (("hdtv",), "HDTV"),
        )
        for markers, type_name in rules:
            if any(marker in filename for marker in markers):
                return type_name
        return ""

    @classmethod
    def _detected_type(cls, filename: str, is_disc: str) -> str:
        matched = cls._matched_release_type(filename)
        if matched:
            return matched
        if is_disc:
            return "DISC"
        return "DVDRIP" if "dvdrip" in filename else "ENCODE"

    async def get_type(
        self, video: str, _scene: bool, is_disc: str, meta: Meta
    ) -> str:
        if meta.manual_type:
            return meta.manual_type
        return self._detected_type(Path(video).name.lower(), is_disc)

    async def is_3d(self, bdinfo: Any | None) -> str:
        if bdinfo is not None:
            if bdinfo["video"][0]["3d"] != "":
                return "3D"
            return ""
        return ""

    async def is_sd(self, resolution: str) -> int:
        return (
            1 if resolution in ("480i", "480p", "576i", "576p", "540p") else 0
        )

    @staticmethod
    def _general_track(meta: Meta) -> dict[str, Any] | None:
        tracks = meta.mediainfo.get("media", {}).get("track", [])
        for raw_track in tracks:
            if not isinstance(raw_track, dict):
                continue
            track = cast(dict[str, Any], raw_track)
            if track.get("@type") == "General":
                return track
        return None

    @staticmethod
    def _mediainfo_duration_minutes(
        track: dict[str, Any] | None,
    ) -> int | None:
        if not track or not track.get("Duration"):
            logger.debug(
                "[red]No valid duration found in MediaInfo General track[/red]"
            )
            return None
        try:
            return int(float(track["Duration"]) // 60)
        except ValueError:
            logger.debug(
                f"[red]Invalid duration value: {track['Duration']}[/red]"
            )
            return None

    @staticmethod
    def _bdinfo_duration_minutes(length: Any) -> int | None:
        if not length:
            logger.debug("[red]No valid duration found in BDInfo[/red]")
            return None
        try:
            hours, minutes, _seconds = str(length).split(":")
            return int(hours) * 60 + int(minutes)
        except ValueError:
            logger.debug(f"[red]Invalid duration value: {length}[/red]")
            return None

    async def get_video_duration(self, meta: Meta) -> int | None:
        if meta.category in ("BOOK", "GAME"):
            return None
        tracks = meta.mediainfo.get("media", {}).get("track")
        if meta.is_disc != "BDMV" and tracks:
            return self._mediainfo_duration_minutes(self._general_track(meta))
        return self._bdinfo_duration_minutes(meta.bdinfo.get("length", ""))

    async def get_container(self, meta: Meta) -> str:
        disc_container = {"BDMV": "m2ts", "HDDVD": "evo", "DVD": "vob"}.get(
            meta.is_disc
        )
        if disc_container:
            return disc_container
        if not meta.filelist:
            logger.info("[red]No files found to determine container[/red]")
            return ""
        try:
            largest_file_path = max(meta.filelist, key=os.path.getsize)
        except (OSError, ValueError) as error:
            logger.error(
                f"[red]Error getting container for file: {error}[/red]"
            )
            return ""
        extension = Path(str(largest_file_path)).suffix
        return extension.lstrip(".").lower()


video_manager = VideoManager()
