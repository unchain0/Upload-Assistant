# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
import shutil
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx
from torf import Torrent

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import artwork_dir
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.observability.runtime_support import logger


class ManualPackageManager:
    def __init__(self, config: Mapping[str, Any]) -> None:
        default_config = cast(Mapping[str, Any], config.get("DEFAULT", {}))
        if not isinstance(default_config, dict):
            raise ValueError("'DEFAULT' config section must be a dict")
        tracker_config = cast(Mapping[str, Any], config.get("TRACKERS", {}))
        if not isinstance(tracker_config, dict):
            raise ValueError("'TRACKERS' config section must be a dict")
        self.default_config = default_config
        self.tracker_config = tracker_config
        self.uploadscreens_manager = UploadScreensManager(
            cast(dict[str, Any], config)
        )

    @staticmethod
    def _state_dir(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid

    @staticmethod
    def _tag_suffix(meta: Meta) -> str:
        if not meta.tag:
            return ""
        return f" / {meta.tag[1:]}"

    @staticmethod
    def _has_tvmaze_id(meta: Meta) -> bool:
        return "tvmaze_id" in meta and meta.tvmaze_id != 0

    async def _write_release_links(self, generic: Any, meta: Meta) -> None:
        if meta.tmdb:
            await generic.write(
                f"TMDB: https://www.themoviedb.org/{meta.category.lower()}/{meta.tmdb}\n"
            )
        if meta.imdb_id != 0:
            await generic.write(
                f"IMDb: https://www.imdb.com/title/tt{meta.imdb_id}\n"
            )
        if meta.tvdb_id != 0:
            await generic.write(
                f"TVDB: https://www.thetvdb.com/?id={meta.tvdb_id}&tab=series\n"
            )
        if self._has_tvmaze_id(meta):
            await generic.write(
                f"TVMaze: https://www.tvmaze.com/shows/{meta.tvmaze_id}\n"
            )

    @staticmethod
    def _should_download_artwork(meta: Meta, poster_path: Path) -> bool:
        return meta.artwork_url not in ["", None] and not poster_path.exists()

    @staticmethod
    def _has_rehosted_poster(meta: Meta, poster_path: Path) -> bool:
        return poster_path.exists() and meta.rehosted_artwork_url is not None

    async def _persist_meta(self, meta: Meta) -> None:
        meta_text = json.dumps(meta.to_dict(), indent=4)
        async with aiofiles.open(
            self._state_dir(meta) / "meta.json", "w"
        ) as metafile:
            await metafile.write(meta_text)

    async def _upload_artwork(
        self, generic: Any, meta: Meta, poster_path: Path
    ) -> None:
        poster, _ = await self.uploadscreens_manager.upload_screens(
            meta, 1, 1, 0, 1, [str(poster_path)], {}
        )
        cover = poster[0]
        cover_url = cover.get("raw_url", cover.get("img_url"))
        await generic.write(f"TMDB Cover: {cover_url}\n")
        meta.rehosted_artwork_url = cover_url

    async def _download_artwork(
        self, generic: Any, meta: Meta, poster_path: Path
    ) -> None:
        if meta.rehosted_artwork_url is not None:
            return
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(meta.artwork_url)
        if response.status_code != 200:
            logger.info("[bold yellow]Cover could not be retrieved")
            return
        logger.info("[bold yellow]Rehosting Cover")
        await asyncio.to_thread(poster_path.write_bytes, response.content)
        if not meta.skip_imghost_upload:
            await self._upload_artwork(generic, meta, poster_path)
        await self._persist_meta(meta)

    async def _write_artwork(self, generic: Any, meta: Meta) -> None:
        poster_path = artwork_dir(meta.base_dir, meta.uuid) / "POSTER.png"
        if self._should_download_artwork(meta, poster_path):
            await self._download_artwork(generic, meta, poster_path)
            return
        if self._has_rehosted_poster(meta, poster_path):
            await generic.write(f"TMDB Cover: {meta.rehosted_artwork_url}\n")

    async def _write_images(self, generic: Any, meta: Meta) -> None:
        if not meta.image_list:
            return
        await generic.write("\nImage Webpage:\n")
        for image in meta.image_list:
            await generic.write(f"{image['web_url']}\n")
        await generic.write("\nThumbnail Image:\n")
        for image in meta.image_list:
            await generic.write(f"{image['img_url']}\n")

    async def _write_generic_info(self, meta: Meta) -> None:
        resolution = meta.source if meta.is_disc == "DVD" else meta.resolution
        generic_path = self._state_dir(meta) / "GENERIC_INFO.txt"
        async with aiofiles.open(
            generic_path, "w", encoding="utf-8"
        ) as generic:
            await generic.write(f"Name: {meta.name}\n\n")
            await generic.write(f"Overview: {meta.overview}\n\n")
            await generic.write(
                f"{resolution} / {meta.type}{self._tag_suffix(meta)}\n\n"
            )
            await generic.write(f"Category: {meta.category}\n")
            await self._write_release_links(generic, meta)
            await self._write_artwork(generic, meta)
            await self._write_images(generic, meta)

    def _prune_extra_torrents(self, meta: Meta) -> None:
        state_dir = self._state_dir(meta)
        torrent_files = list(state_dir.glob("*.torrent"))
        if len(torrent_files) <= 1:
            return
        for torrent_path in torrent_files:
            if not torrent_path.name.startswith(("BASE", "[RAND")):
                torrent_path.resolve().unlink()

    def _copy_base_torrent(self, meta: Meta) -> None:
        base_path = self._state_dir(meta) / "BASE.torrent"
        if not base_path.exists():
            return
        base_torrent = Torrent.read(str(base_path))
        manual_name = re.sub(
            r"[^0-9a-zA-Z\[\]\'\-]+", ".", Path(meta.path or "").name
        )
        Torrent.copy(base_torrent).write(
            str(self._state_dir(meta) / f"{manual_name}.torrent"),
            overwrite=True,
        )

    def _manual_filebrowser(self) -> str | None:
        manual_tracker = self.tracker_config.get("MANUAL")
        if not isinstance(manual_tracker, dict):
            return None
        manual_tracker_config = cast(dict[str, Any], manual_tracker)
        filebrowser = manual_tracker_config.get("filebrowser")
        if not isinstance(filebrowser, str):
            return None
        return filebrowser

    async def _upload_archive(self, meta: Meta, archive: str) -> str:
        tar_bytes = await asyncio.to_thread(Path(f"{archive}.tar").read_bytes)
        files = {"files[]": (f"{meta.title}.tar", tar_bytes)}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = (
                await client.post("https://uguu.se/upload.php", files=files)
            ).json()
        logger.debug(f"[cyan]{response}")
        return cast(str, response["files"][0]["url"])

    async def _archive_url(self, meta: Meta) -> str:
        title = re.sub(r"[^0-9a-zA-Z\[\\]]+", "", meta.title)
        archive = str(self._state_dir(meta) / title)
        shutil.make_archive(archive, "tar", str(self._state_dir(meta)))
        filebrowser = self._manual_filebrowser()
        if filebrowser is None:
            return await self._upload_archive(meta, archive)
        path = f"/tmp/{meta.uuid}"  # noqa: S108  # nosec B108 -- remote FileBrowser URL path, not local storage
        return filebrowser.rstrip("/") + urllib.parse.quote(path, safe="/")

    async def package(self, meta: Meta) -> str | bool:
        await self._write_generic_info(meta)
        self._prune_extra_torrents(meta)
        try:
            self._copy_base_torrent(meta)
            return await self._archive_url(meta)
        except Exception:
            return False
