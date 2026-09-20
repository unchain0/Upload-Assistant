import asyncio
import contextlib
import json
import platform
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from PIL import Image

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import menu_screenshots_dir
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.media.media_info import MediaInfo
from src.integrations.media.screenshot_capture import (
    screenshot_par_scale_factors,
    should_scale_screenshots_for_par,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)


def _selection_indices(length: int, num_to_select: int) -> list[int]:
    if num_to_select <= 0:
        return []
    if num_to_select == 1:
        return [0]
    return [
        round(index * (length - 1) / (num_to_select - 1))
        for index in range(num_to_select)
    ]


def select_evenly_spaced(items: list[Any], num_to_select: int) -> list[Any]:
    if len(items) <= num_to_select:
        return items
    indices = _selection_indices(len(items), num_to_select)
    return [items[index] for index in indices]


def discard_previous_menu_capture_files(image_pattern: Path) -> None:
    """Remove only prior output for the menu VOB about to be captured."""
    glob_name = image_pattern.name.replace("%03d", "*")
    for image_path in image_pattern.parent.glob(glob_name):
        with contextlib.suppress(OSError):
            image_path.unlink()


@dataclass(frozen=True)
class _MenuVideoInfo:
    width: int
    height: int
    par: float
    dar: float
    duration_ms: Any


@dataclass(frozen=True)
class _MenuCapturePaths:
    image_pattern: Path
    glob_pattern: Path
    single_image: Path


@dataclass(frozen=True)
class _FfmpegResult:
    returncode: int | None
    stderr: bytes


class DiscMenus:
    """
    Handles the processing and uploading of disc menu images.
    """

    def __init__(self, meta: Meta, config: MutableMapping[str, Any]):
        self.config = config
        self.path_to_menu_screenshots = meta.path_to_menu_screenshots or ""
        self.uploadscreens_manager = UploadScreensManager(
            cast(dict[str, Any], config)
        )

    def _resolved_menu_source(self) -> str | None:
        if self.path_to_menu_screenshots:
            return self.path_to_menu_screenshots
        default_section = self.config.get("DEFAULT", {})
        if hasattr(default_section, "get") and default_section.get(
            "auto_dvd_menus", False
        ):
            return "auto"
        return None

    async def get_disc_menu_images(self, meta: Meta) -> None:
        """Processes disc menu images from a local directory and uploads them."""
        menu_source = self._resolved_menu_source()
        if menu_source is None:
            return
        self.path_to_menu_screenshots = menu_source
        if menu_source.lower() == "auto":
            await self.auto_capture_dvd_menus(meta)
            return
        if Path(menu_source).is_dir():
            await self.get_local_images(meta)
            return
        logger.info(f"[red]Invalid disc menus path: {menu_source}[/red]")

    @staticmethod
    def _supported_discs(meta: Meta) -> bool:
        return any(disc.get("type") in ("DVD", "HDDVD") for disc in meta.discs)

    def _default_settings(self) -> Mapping[str, Any]:
        section = self.config.get("DEFAULT", {})
        if isinstance(section, Mapping):
            return cast(Mapping[str, Any], section)
        return {}

    @staticmethod
    def _max_menu_screens(defaults: Mapping[str, Any]) -> int:
        try:
            return int(defaults.get("max_menu_screens", 6))
        except ValueError, TypeError:
            return 6

    @staticmethod
    def _ffmpeg_arch(machine: str) -> str | None:
        if machine in ("x86_64", "amd64"):
            return "amd"
        if machine in ("aarch64", "arm64"):
            return "arm"
        return None

    @classmethod
    def _bundled_ffmpeg(cls, meta: Meta) -> str | None:
        if platform.system() != "Linux":
            return None
        arch = cls._ffmpeg_arch(platform.machine().lower())
        if arch is None:
            return None
        candidate = Path(meta.base_dir) / "bin" / "ffmpeg" / arch / "ffmpeg"
        if candidate.exists():
            return str(candidate)
        return None

    def _ffmpeg_path(self, meta: Meta) -> str:
        configured = configured_binary("ffmpeg_path", self.config)
        if configured is not None:
            return configured
        return self._bundled_ffmpeg(meta) or "ffmpeg"

    @staticmethod
    def _round_to_even(value: float) -> int:
        rounded = round(value)
        if rounded % 2 != 0:
            return rounded + 1
        return rounded

    @staticmethod
    def _is_menu_vob(filename: str) -> bool:
        upper = filename.upper()
        if upper == "VIDEO_TS.VOB":
            return True
        return re.match(r"^VTS_\d{2}_0\.VOB$", upper) is not None

    @classmethod
    def _menu_file(
        cls, disc_path: Path, filename: str
    ) -> tuple[str, Path] | None:
        if not filename.lower().endswith(".vob"):
            return None
        if not cls._is_menu_vob(filename):
            return None
        file_path = disc_path / filename
        if not file_path.is_file():
            return None
        if file_path.stat().st_size <= 50000:
            return None
        return filename, file_path

    @classmethod
    def _menu_files(cls, disc_path: Path) -> list[tuple[str, Path]] | None:
        menu_files: list[tuple[str, Path]] = []
        try:
            for path in disc_path.iterdir():
                menu_file = cls._menu_file(disc_path, path.name)
                if menu_file is not None:
                    menu_files.append(menu_file)
        except Exception as exc:
            logger.error(
                f"[red]Error scanning directory {disc_path} for menus: {exc}[/red]"
            )
            return None
        menu_files.sort(key=lambda item: item[0].upper())
        return menu_files

    @staticmethod
    def _video_track(media_info: Any) -> Any | None:
        for track in media_info.tracks:
            if track.track_type == "Video":
                return track
        return None

    @staticmethod
    def _track_number(value: Any, default: float) -> float:
        if value is None:
            return default
        return float(value)

    @classmethod
    def _video_info(
        cls, filename: str, file_path: Path
    ) -> _MenuVideoInfo | None:
        try:
            media_info = MediaInfo.parse(file_path)
            video_track = cls._video_track(media_info)
            if video_track is None:
                logger.debug(
                    f"Skipping {filename} because it does not have a video track."
                )
                return None
            return _MenuVideoInfo(
                width=int(cls._track_number(video_track.width, 720)),
                height=int(cls._track_number(video_track.height, 480)),
                par=cls._track_number(video_track.pixel_aspect_ratio, 1.0),
                dar=cls._track_number(
                    video_track.display_aspect_ratio, 1.3333
                ),
                duration_ms=video_track.duration,
            )
        except Exception as exc:
            logger.error(
                f"[red]Error parsing MediaInfo for {filename}: {exc}[/red]"
            )
            return _MenuVideoInfo(720, 480, 1.0, 1.3333, None)

    @staticmethod
    def _duration_seconds(duration_ms: Any) -> float:
        if not duration_ms:
            return 0.0
        try:
            return float(duration_ms) / 1000.0
        except ValueError, TypeError:
            return 0.0

    @classmethod
    def _video_filters(
        cls,
        info: _MenuVideoInfo,
        duration_sec: float,
        scale_for_par: bool,
    ) -> list[str]:
        width_scale, height_scale = screenshot_par_scale_factors(
            info.width,
            info.height,
            info.par,
            info.dar,
            scale_for_par,
        )
        filters: list[str] = []
        if duration_sec < 2.0:
            filters.append("mpdecimate")
        if width_scale != 1 or height_scale != 1:
            scaled_width = cls._round_to_even(info.width * width_scale)
            scaled_height = cls._round_to_even(info.height * height_scale)
            filters.append(f"scale={scaled_width}:{scaled_height}")
        filters.append("format=rgb24")
        return filters

    @staticmethod
    def _sanitized_disc_name(disc: Mapping[str, Any]) -> str:
        return re.sub(r'[<>:"/\\|?*]', "_", str(disc.get("name", "dvd")))

    @classmethod
    def _capture_paths(
        cls, output_dir: Path, disc: Mapping[str, Any], filename: str
    ) -> _MenuCapturePaths:
        disc_name = cls._sanitized_disc_name(disc)
        vob_base = Path(filename).stem
        prefix = f"{disc_name}-{vob_base}"
        return _MenuCapturePaths(
            image_pattern=output_dir / f"{prefix}-%03d.png",
            glob_pattern=output_dir / f"{prefix}-*.png",
            single_image=output_dir / f"{prefix}-001.png",
        )

    @staticmethod
    def _initial_command(
        ffmpeg_path: str,
        file_path: Path,
        paths: _MenuCapturePaths,
        filters: list[str],
        duration_sec: float,
        max_menu_screens: int,
        filename: str,
    ) -> list[str]:
        if duration_sec < 2.0:
            limit = max(10, max_menu_screens)
            logger.info(
                f"Extracting static menu frames from {filename} (limit: {limit})..."
            )
            return [
                ffmpeg_path,
                "-y",
                "-t",
                "5.0",
                "-i",
                str(file_path),
                "-vf",
                ",".join(filters),
                "-fps_mode",
                "passthrough",
                "-vframes",
                str(limit),
                str(paths.image_pattern),
            ]
        limit = max(30, max_menu_screens * 3)
        scene_filters = ",".join(["select='gt(scene,0.25)'", *filters])
        logger.info(
            f"Extracting motion menu frames via scene detection from {filename} (limit: {limit})..."
        )
        return [
            ffmpeg_path,
            "-y",
            "-i",
            str(file_path),
            "-vf",
            scene_filters,
            "-fps_mode",
            "vfr",
            "-vframes",
            str(limit),
            str(paths.image_pattern),
        ]

    @staticmethod
    async def _run_ffmpeg(
        command: list[str], timeout_seconds: float, timeout_message: str
    ) -> tuple[asyncio.subprocess.Process, _FfmpegResult]:
        # ffmpeg executable is resolved from validated local config/bundle; argv is exec-form.
        process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                process.kill()
            _stdout, stderr = await process.communicate()
            logger.error(timeout_message)
        return process, _FfmpegResult(process.returncode, stderr)

    @staticmethod
    def _glob_images(
        paths: _MenuCapturePaths, returncode: int | None
    ) -> list[str]:
        if returncode != 0:
            return []
        return sorted(
            str(path)
            for path in paths.glob_pattern.parent.glob(paths.glob_pattern.name)
        )

    @staticmethod
    def _black_frame(path: str) -> bool:
        with Image.open(path) as image:
            extrema = image.convert("L").getextrema()
            if not extrema:
                return False
            maximum = extrema[1]
            return isinstance(maximum, (int, float)) and maximum < 10

    @staticmethod
    def _black_frame_skip_message(image_path: str, phase: str) -> str:
        if phase:
            return (
                f"Skipping {phase} frame {Path(image_path).name} because it is "
                "a blank/black frame."
            )
        return f"Skipping {Path(image_path).name} because it is a blank/black frame."

    @staticmethod
    def _black_frame_error_message(
        image_path: str, phase: str, exc: Exception
    ) -> str:
        if phase:
            return f"Failed to check if {phase} frame {image_path} is black: {exc}"
        return f"Failed to check if {image_path} is black: {exc}"

    @classmethod
    def _keep_frame(cls, image_path: str, phase: str) -> bool:
        try:
            if not cls._black_frame(image_path):
                return True
            logger.debug(cls._black_frame_skip_message(image_path, phase))
            Path(image_path).unlink()
            return False
        except Exception as exc:
            logger.debug(
                cls._black_frame_error_message(image_path, phase, exc)
            )
            return True

    @classmethod
    def _filter_black_frames(
        cls, images: list[str], phase: str = ""
    ) -> list[str]:
        return [image for image in images if cls._keep_frame(image, phase)]

    @staticmethod
    def _log_ffmpeg_failure(
        filename: str,
        phase: str,
        result: _FfmpegResult,
        paths: _MenuCapturePaths,
    ) -> None:
        if result.returncode == 0:
            return
        phase_label = f" {phase}" if phase else ""
        logger.error(
            f"[red]FFmpeg{phase_label} failed processing {filename}: "
            f"{result.stderr.decode(errors='replace')}[/red]"
        )
        discard_previous_menu_capture_files(paths.image_pattern)

    @classmethod
    async def _initial_capture(
        cls,
        ffmpeg_path: str,
        file_path: Path,
        filename: str,
        paths: _MenuCapturePaths,
        filters: list[str],
        duration_sec: float,
        max_menu_screens: int,
    ) -> list[str]:
        command = cls._initial_command(
            ffmpeg_path,
            file_path,
            paths,
            filters,
            duration_sec,
            max_menu_screens,
            filename,
        )
        logger.debug(f"FFmpeg command: {' '.join(command)}")
        process, result = await cls._run_ffmpeg(
            command,
            30.0,
            f"[red]FFmpeg timed out processing {filename}[/red]",
        )
        images = cls._glob_images(paths, process.returncode)
        cls._log_ffmpeg_failure(filename, "", result, paths)
        return cls._filter_black_frames(images)

    @staticmethod
    def _fallback_command(
        ffmpeg_path: str,
        file_path: Path,
        paths: _MenuCapturePaths,
        filters: list[str],
        max_menu_screens: int,
    ) -> list[str]:
        fallback_filters = ",".join(["fps=1/5", *filters])
        return [
            ffmpeg_path,
            "-y",
            "-ss",
            "2.0",
            "-i",
            str(file_path),
            "-vf",
            fallback_filters,
            "-fps_mode",
            "vfr",
            "-vframes",
            str(max_menu_screens),
            str(paths.image_pattern),
        ]

    @classmethod
    async def _fallback_capture(
        cls,
        ffmpeg_path: str,
        file_path: Path,
        filename: str,
        paths: _MenuCapturePaths,
        filters: list[str],
        max_menu_screens: int,
    ) -> list[str]:
        logger.info(
            f"Scene detection returned no valid frames for {filename}. "
            "Falling back to interval sampling..."
        )
        command = cls._fallback_command(
            ffmpeg_path, file_path, paths, filters, max_menu_screens
        )
        logger.debug(f"Fallback FFmpeg command: {' '.join(command)}")
        discard_previous_menu_capture_files(paths.image_pattern)
        process, result = await cls._run_ffmpeg(
            command,
            30.0,
            f"[red]FFmpeg fallback timed out processing {filename}[/red]",
        )
        images = cls._glob_images(paths, process.returncode)
        cls._log_ffmpeg_failure(filename, "fallback", result, paths)
        return cls._filter_black_frames(images, "fallback")

    @staticmethod
    def _retry_command(
        ffmpeg_path: str,
        file_path: Path,
        paths: _MenuCapturePaths,
        filters: list[str],
    ) -> list[str]:
        return [
            ffmpeg_path,
            "-y",
            "-i",
            str(file_path),
            "-vframes",
            "1",
            "-vf",
            ",".join(filters),
            "-update",
            "1",
            str(paths.single_image),
        ]

    @classmethod
    async def _retry_capture(
        cls,
        ffmpeg_path: str,
        file_path: Path,
        filename: str,
        paths: _MenuCapturePaths,
        filters: list[str],
    ) -> list[str]:
        logger.debug(
            f"FFmpeg fallback/scene detection failed for {filename}. "
            "Retrying from start (seek_time=0)."
        )
        command = cls._retry_command(ffmpeg_path, file_path, paths, filters)
        _process, _result = await cls._run_ffmpeg(
            command,
            15.0,
            f"[red]FFmpeg retry timed out processing {filename}[/red]",
        )
        images = sorted(
            str(path)
            for path in paths.glob_pattern.parent.glob(paths.glob_pattern.name)
        )
        return cls._filter_black_frames(images, "retry")

    @classmethod
    async def _capture_motion_fallbacks(
        cls,
        images: list[str],
        ffmpeg_path: str,
        file_path: Path,
        filename: str,
        paths: _MenuCapturePaths,
        filters: list[str],
        max_menu_screens: int,
    ) -> list[str]:
        if images:
            return images
        images = await cls._fallback_capture(
            ffmpeg_path,
            file_path,
            filename,
            paths,
            filters,
            max_menu_screens,
        )
        if images:
            return images
        return await cls._retry_capture(
            ffmpeg_path, file_path, filename, paths, filters
        )

    @classmethod
    async def _capture_menu_file(
        cls,
        disc: Mapping[str, Any],
        filename: str,
        file_path: Path,
        output_dir: Path,
        ffmpeg_path: str,
        max_menu_screens: int,
        scale_for_par: bool,
    ) -> list[str]:
        info = cls._video_info(filename, file_path)
        if info is None:
            return []
        duration_sec = cls._duration_seconds(info.duration_ms)
        filters = cls._video_filters(info, duration_sec, scale_for_par)
        paths = cls._capture_paths(output_dir, disc, filename)
        discard_previous_menu_capture_files(paths.image_pattern)
        try:
            images = await cls._initial_capture(
                ffmpeg_path,
                file_path,
                filename,
                paths,
                filters,
                duration_sec,
                max_menu_screens,
            )
            if duration_sec >= 2.0:
                images = await cls._capture_motion_fallbacks(
                    images,
                    ffmpeg_path,
                    file_path,
                    filename,
                    paths,
                    filters,
                    max_menu_screens,
                )
        except Exception as exc:
            logger.error(
                f"[red]Error running ffmpeg for {filename}: {exc}[/red]"
            )
            return []
        if images:
            logger.info(
                f"[green]Successfully captured {len(images)} menu screenshot(s) "
                f"for {filename}[/green]"
            )
            return images
        logger.info(
            f"[yellow]No valid menu frames captured for {filename} "
            "(file may contain only blank/black placeholder screens)[/yellow]"
        )
        return []

    @staticmethod
    def _dvd_path(disc: Mapping[str, Any]) -> Path | None:
        disc_path = disc.get("path")
        if not disc_path:
            return None
        path = Path(str(disc_path))
        if not path.is_dir():
            return None
        return path

    @classmethod
    def _disc_menu_files(
        cls, disc: Mapping[str, Any]
    ) -> list[tuple[str, Path]] | None:
        disc_type = disc.get("type")
        if disc_type == "HDDVD":
            logger.warning(
                "[yellow]HD-DVD menu capture is not supported. "
                f"Skipping HD-DVD: {disc.get('name', 'Unknown')}[/yellow]"
            )
            return None
        if disc_type != "DVD":
            return None
        disc_path = cls._dvd_path(disc)
        if disc_path is None:
            return None
        return cls._menu_files(disc_path)

    @classmethod
    async def _capture_disc(
        cls,
        disc: Mapping[str, Any],
        output_dir: Path,
        ffmpeg_path: str,
        max_menu_screens: int,
        scale_for_par: bool,
    ) -> list[str]:
        menu_files = cls._disc_menu_files(disc)
        if menu_files is None:
            return []
        captured: list[str] = []
        for filename, file_path in menu_files:
            captured.extend(
                await cls._capture_menu_file(
                    disc,
                    filename,
                    file_path,
                    output_dir,
                    ffmpeg_path,
                    max_menu_screens,
                    scale_for_par,
                )
            )
        return captured

    @staticmethod
    def _limit_captured_images(images: list[str], maximum: int) -> list[str]:
        if len(images) <= maximum:
            return images
        logger.info(
            f"[yellow]Captured {len(images)} screenshots, limiting to {maximum} "
            "(configured by max_menu_screens) using even spacing.[/yellow]"
        )
        keep_images = select_evenly_spaced(images, maximum)
        keep_set = set(keep_images)
        for image in images:
            if image not in keep_set:
                with contextlib.suppress(Exception):
                    Path(image).unlink()
        return keep_images

    async def _upload_captured_images(
        self, meta: Meta, captured_images: list[str]
    ) -> None:
        logger.info(
            f"[cyan]Uploading {len(captured_images)} auto-captured disc menu "
            "screenshots...[/cyan]"
        )
        uploaded_images, _ = await self.uploadscreens_manager.upload_screens(
            meta,
            screens=len(captured_images),
            img_host_num=1,
            i=0,
            total_screens=len(captured_images),
            custom_img_list=captured_images,
            return_dict={},
            retry_mode=False,
        )
        meta.menu_images = uploaded_images
        await self.save_images_to_json(meta, uploaded_images)

    async def auto_capture_dvd_menus(self, meta: Meta) -> None:
        """Automatically capture DVD menu images and upload them."""
        if not self._supported_discs(meta):
            logger.debug(
                "No supported DVD/HDDVD discs found in metadata; "
                "skipping menu auto-capture."
            )
            return
        defaults = self._default_settings()
        max_menu_screens = self._max_menu_screens(defaults)
        scale_for_par = should_scale_screenshots_for_par(defaults)
        output_dir = menu_screenshots_dir(meta.base_dir, meta.uuid)
        ffmpeg_path = self._ffmpeg_path(meta)
        captured_images: list[str] = []
        for disc in meta.discs:
            captured_images.extend(
                await self._capture_disc(
                    cast(Mapping[str, Any], disc),
                    output_dir,
                    ffmpeg_path,
                    max_menu_screens,
                    scale_for_par,
                )
            )
        captured_images = self._limit_captured_images(
            captured_images, max_menu_screens
        )
        if not captured_images:
            logger.info(
                "[yellow]No disc menu images could be auto-captured.[/yellow]"
            )
            return
        await self._upload_captured_images(meta, captured_images)

    async def get_local_images(self, meta: Meta) -> None:
        """
        Uploads disc menu images from a local directory.
        """
        image_paths = [
            p
            for p in Path(self.path_to_menu_screenshots).iterdir()
            if p.name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        if not image_paths:
            logger.info(
                "[yellow]No local menu images found to upload.[/yellow]"
            )
            return

        uploaded_images, _ = await self.uploadscreens_manager.upload_screens(
            meta,
            screens=len(image_paths),
            img_host_num=1,
            i=0,
            total_screens=len(image_paths),
            custom_img_list=image_paths,
            return_dict={},
            retry_mode=False,
        )
        meta.menu_images = uploaded_images

        await self.save_images_to_json(meta, uploaded_images)

    async def save_images_to_json(
        self, meta: Meta, image_list: Sequence[dict[str, Any]]
    ) -> None:
        """
        Saves the uploaded disc menu images to a JSON file.
        """
        if not image_list:
            logger.info("[yellow]No menu images found.[/yellow]")
            return

        menu_images = {"menu_images": list(image_list)}

        base_dir = meta.base_dir
        uuid_value = meta.uuid
        json_path = Path(base_dir) / "tmp" / uuid_value / "menu_images.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)

        menu_json = json.dumps(menu_images, indent=4)
        await asyncio.to_thread(Path(json_path).write_text, menu_json)

        logger.info(
            f"[green]Saved {len(image_list)} menu images to {json_path}[/green]"
        )


async def process_disc_menus(
    meta: Meta, config: MutableMapping[str, Any]
) -> None:
    """
    Main function to process disc menu images.
    """
    disc_menus = DiscMenus(meta, config)
    await disc_menus.get_disc_menu_images(meta)
