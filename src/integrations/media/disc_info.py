# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import itertools
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import aiofiles

from src.domain_models.release import Meta
from src.integrations.media.disc_parser import DiscParse
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.bdinfo import BDInfoBinaryManager
from src.integrations.runtime_tools.dvd_media_info import (
    download_dvd_mediainfo,
)

Disc = dict[str, Any]


class DiscInfoManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self._parser = DiscParse(config)

    @staticmethod
    def _disc_entry(path: str, directory: str) -> tuple[str, Disc] | None:
        common = {"path": f"{path}/{directory}", "name": Path(path).name}
        if directory.upper() == "BDMV":
            return "BDMV", {
                **common,
                "type": "BDMV",
                "summary": "",
                "bdinfo": "",
            }
        if directory == "VIDEO_TS":
            return "DVD", {
                **common,
                "type": "DVD",
                "vob_mi": "",
                "ifo_mi": "",
                "main_set": [],
                "size": "",
            }
        if directory == "HVDVD_TS":
            return "HDDVD", {
                **common,
                "type": "HDDVD",
                "evo_mi": "",
                "largest_evo": "",
            }
        return None

    @classmethod
    def _discover_discs(cls, base_path: str) -> tuple[str, list[Disc]]:
        is_disc = ""
        discs: list[Disc] = []
        for path, directories, _files in sorted(os.walk(base_path)):
            for directory in directories:
                entry = cls._disc_entry(path, directory)
                if entry is None:
                    continue
                is_disc, disc = entry
                discs.append(disc)
        return is_disc, discs

    @staticmethod
    def _reject_bdmv_site_check(meta: Meta) -> None:
        if not meta.site_check:
            return
        logger.info(
            "BDMV disc checking is not supported in site_check mode, yet.",
            extra={"markup": False},
        )
        raise RuntimeError(
            "BDMV disc checking is not supported in site_check mode."
        )

    @staticmethod
    async def _ensure_bdinfo(meta: Meta) -> None:
        try:
            await BDInfoBinaryManager.ensure_bdinfo_binary(
                meta.base_dir, "v0.3.1"
            )
        except Exception as error:
            logger.error(
                f"[red]Failed to ensure bdinfo binary: {error}[/red]",
                extra={"markup": False},
            )
            raise

    @staticmethod
    def _bdinfo_input_discs(meta: Meta, discovered: list[Disc]) -> list[Disc]:
        return discovered if meta.edit is False else meta.discs

    async def _process_bdmv(
        self, meta: Meta, discs: list[Disc]
    ) -> tuple[list[Disc], Any]:
        self._reject_bdmv_site_check(meta)
        await self._ensure_bdinfo(meta)
        source_discs = self._bdinfo_input_discs(meta, discs)
        return await self._parser.get_bdinfo(
            meta, source_discs, meta.uuid, meta.base_dir, meta.discs
        )

    async def _process_dvd(self, meta: Meta, discs: list[Disc]) -> list[Disc]:
        download_dvd_mediainfo(meta.base_dir)
        return cast(
            list[Disc],
            await cast(Any, self._parser).get_dvdinfo(
                discs, base_dir=meta.base_dir
            ),
        )

    @staticmethod
    async def _write_hddvd_mediainfo(meta: Meta, discs: list[Disc]) -> None:
        output = Path(meta.base_dir) / "tmp" / meta.uuid / "MEDIAINFO.txt"
        async with aiofiles.open(
            output,
            "w",
            newline="",
            encoding="utf-8",
        ) as export:
            await export.write(discs[0]["evo_mi"])

    async def _process_hddvd(
        self, meta: Meta, discs: list[Disc]
    ) -> list[Disc]:
        processed = await self._parser.get_hddvd_info(discs, meta)
        await self._write_hddvd_mediainfo(meta, processed)
        return processed

    async def _process_discs(
        self, is_disc: str, meta: Meta, discs: list[Disc]
    ) -> tuple[list[Disc], Any]:
        if is_disc == "BDMV":
            return await self._process_bdmv(meta, discs)
        if is_disc == "DVD":
            return await self._process_dvd(meta, discs), None
        if is_disc == "HDDVD":
            return await self._process_hddvd(meta, discs), None
        return discs, None

    async def get_disc(self, meta: Meta) -> tuple[str, str, Any, list[Disc]]:
        base_path = str(meta.path)
        is_disc, discs = self._discover_discs(base_path)
        discs, bdinfo = await self._process_discs(is_disc, meta, discs)
        discs = sorted(discs, key=lambda disc: disc["name"])
        return is_disc, base_path, bdinfo, discs

    @staticmethod
    def _grouped_dvd_sizes(discs: Iterable[Disc]) -> list[list[str]]:
        sizes = sorted(str(disc["size"]) for disc in discs)
        return [list(group) for _key, group in itertools.groupby(sizes)]

    @staticmethod
    def _dvd_size_label(group: list[str]) -> str:
        return f"{len(group)}x{group[0]}" if len(group) > 1 else group[0]

    @classmethod
    def _compact_dvd_sizes(cls, discs: Iterable[Disc]) -> str:
        labels = [
            cls._dvd_size_label(group)
            for group in cls._grouped_dvd_sizes(discs)
        ]
        return " ".join(sorted(labels))

    async def get_dvd_size(
        self, discs: Iterable[Disc], manual_dvds: str | None
    ) -> str:
        if manual_dvds:
            return str(manual_dvds)
        return self._compact_dvd_sizes(discs)
