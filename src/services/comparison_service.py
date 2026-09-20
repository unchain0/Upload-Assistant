# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import cli_ui

from src.domain_models.release import Meta
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.observability.runtime_support import logger

ComparisonGroup = dict[str, Any]
ComparisonData = dict[str, ComparisonGroup]
SavedComparisonData = ComparisonData | list[ComparisonGroup]
ComparisonGroups = defaultdict[str, list[tuple[int, str]]]


class ComparisonManager:
    def __init__(self, meta: Meta, config: Mapping[str, Any]) -> None:
        self.meta = meta
        default_config = cast(Mapping[str, Any], config.get("DEFAULT", {}))
        if not isinstance(default_config, dict):
            raise ValueError("'DEFAULT' config section must be a dict")
        self.default_config = default_config
        self.uploadscreens_manager = UploadScreensManager(
            cast(dict[str, Any], config)
        )

    def _comparison_data_file(self) -> Path:
        return (
            Path(self.meta.base_dir)
            / "tmp"
            / self.meta.uuid
            / "comparison_data.json"
        )

    @staticmethod
    def _saved_dict(raw_data: dict[str, Any]) -> ComparisonData:
        if not all(isinstance(value, dict) for value in raw_data.values()):
            raise ValueError(
                "Invalid comparison data format: must be a dict of dicts"
            )
        return cast(ComparisonData, raw_data)

    @staticmethod
    def _saved_list(raw_data: list[Any]) -> list[ComparisonGroup]:
        if not all(isinstance(item, dict) for item in raw_data):
            raise ValueError(
                "Invalid comparison data format: must be a list of dicts"
            )
        return cast(list[ComparisonGroup], raw_data)

    @classmethod
    def _validated_saved_data(cls, raw_data: Any) -> SavedComparisonData:
        if isinstance(raw_data, dict):
            return cls._saved_dict(cast(dict[str, Any], raw_data))
        if isinstance(raw_data, list):
            return cls._saved_list(cast(list[Any], raw_data))
        raise ValueError(
            "Invalid comparison data format: must be a dict of dicts or a list of dicts"
        )

    @staticmethod
    def _group_urls(group: ComparisonGroup) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], group.get("urls", []))

    def _saved_dict_urls(
        self, saved_data: ComparisonData, comparison_index: str
    ) -> list[dict[str, Any]] | None:
        group = saved_data.get(comparison_index)
        if group is not None:
            return self._group_urls(group)
        logger.info(
            f"[yellow]Comparison index '{comparison_index}' not found in saved data; available keys: {list(saved_data.keys())}[/yellow]"
        )
        return None

    def _saved_list_urls(
        self, saved_data: list[ComparisonGroup], comparison_index: str
    ) -> list[dict[str, Any]] | None:
        try:
            index = int(comparison_index)
        except ValueError:
            logger.info(
                f"[yellow]Comparison index '{comparison_index}' is not a valid integer for list data[/yellow]"
            )
            return None
        if 0 <= index < len(saved_data):
            return self._group_urls(saved_data[index])
        logger.info(
            f"[yellow]Comparison index '{comparison_index}' out of range; valid range: 0-{len(saved_data) - 1}[/yellow]"
        )
        return None

    def _append_unique_urls(
        self, urls: list[dict[str, Any]], comparison_index: str
    ) -> None:
        if self.meta.debug:
            logger.debug(
                f"[cyan]Adding {len(urls)} images from comparison group {comparison_index} to image_list"
            )
        image_list = self.meta.image_list
        for url_info in urls:
            if url_info not in image_list:
                image_list.append(url_info)

    def _apply_saved_selection(self, saved_data: SavedComparisonData) -> None:
        comparison_index = self.meta.comparison_index
        if comparison_index is None:
            return
        comparison_index_str = str(comparison_index).strip()
        if isinstance(saved_data, dict):
            urls = self._saved_dict_urls(saved_data, comparison_index_str)
        else:
            urls = self._saved_list_urls(saved_data, comparison_index_str)
        if urls:
            self._append_unique_urls(urls, comparison_index_str)

    async def _load_saved_comparison(
        self, comparison_data_file: Path
    ) -> SavedComparisonData | None:
        if not comparison_data_file.exists():
            return None
        try:
            raw_text = await asyncio.to_thread(comparison_data_file.read_text)
            saved_data = self._validated_saved_data(json.loads(raw_text))
            if self.meta.debug:
                logger.debug(
                    f"[cyan]Loading previously saved comparison data from {comparison_data_file}"
                )
            self.meta.comparison_groups = saved_data
            self._apply_saved_selection(saved_data)
            return saved_data
        except Exception as exc:
            logger.info(f"[yellow]Error loading saved comparison data: {exc}")
            return None

    @staticmethod
    def _comparison_files(comparison_path: Path) -> list[str]:
        return [
            path.name
            for path in comparison_path.iterdir()
            if path.name.lower().endswith(".png")
        ]

    @staticmethod
    def _group_comparison_files(
        files: list[str],
    ) -> tuple[ComparisonGroups, dict[str, str]]:
        pattern = re.compile(r"(\d+)-(\d+)-(.+)\.png", re.IGNORECASE)
        groups: ComparisonGroups = defaultdict(list)
        suffixes: dict[str, str] = {}
        for filename in files:
            match = pattern.match(filename)
            if match is None:
                continue
            first, second, suffix = match.groups()
            groups[second].append((int(first), filename))
            suffixes.setdefault(second, suffix)
        return groups, suffixes

    def _image_host_indices(self) -> list[int]:
        indices = [
            int(key.split("_")[-1])
            for key in self.default_config
            if key.startswith("img_host_") and key.split("_")[-1].isdigit()
        ]
        indices.sort()
        if not indices:
            raise ValueError(
                "No image hosts found in config. Please ensure at least one 'img_host_X' key is present in config."
            )
        return indices

    def _image_host_name(self, host_number: int) -> str | None:
        value = self.default_config.get(f"img_host_{host_number}")
        if value is None or isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _uploaded_infos(
        upload_result: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {key: item.get(key) for key in ("img_url", "raw_url", "web_url")}
            for item in upload_result
        ]

    async def _upload_comparison_group(
        self,
        comparison_path: Path,
        second: str,
        group: list[tuple[int, str]],
        suffix: str,
        host_number: int,
    ) -> ComparisonGroup:
        ordered_group = sorted(group, key=lambda item: item[0])
        group_files = [filename for _, filename in ordered_group]
        custom_img_list = [
            str(comparison_path / filename) for filename in group_files
        ]
        logger.info(
            f"[cyan]Uploading comparison group {second} with files: {group_files}"
        )
        upload_result, _ = await self.uploadscreens_manager.upload_screens(
            self.meta.copy(),
            len(custom_img_list),
            host_number,
            0,
            len(custom_img_list),
            custom_img_list,
            {},
        )
        uploaded_infos = self._uploaded_infos(
            cast(list[Mapping[str, Any]], upload_result)
        )
        return {
            "files": group_files,
            "urls": uploaded_infos,
            "img_host": self._image_host_name(host_number),
            "name": suffix,
        }

    async def _build_comparison_data(
        self, comparison_path: Path
    ) -> ComparisonData:
        files = self._comparison_files(comparison_path)
        groups, suffixes = self._group_comparison_files(files)
        host_number = self._image_host_indices()[0]
        comparisons: ComparisonData = {}
        for second in sorted(groups, key=int):
            comparisons[second] = await self._upload_comparison_group(
                comparison_path,
                second,
                groups[second],
                suffixes.get(second, ""),
                host_number,
            )
        return comparisons

    @staticmethod
    def _prompt_comparison_index() -> str:
        logger.info(
            "[red]No comparison index provided. Please specify a comparison index matching the input file."
        )
        while True:
            cli_input = (
                cli_ui.ask_string("Enter comparison index number: ") or ""
            )
            try:
                return str(int(cli_input.strip()))
            except Exception:
                logger.info(
                    f"[red]Invalid comparison index: {cli_input.strip()}"
                )

    def _generated_comparison_index(self) -> str:
        comparison_index = self.meta.comparison_index
        if comparison_index is None:
            return self._prompt_comparison_index()
        return str(comparison_index).strip()

    def _apply_generated_selection(self, comparisons: ComparisonData) -> None:
        comparison_index = self._generated_comparison_index()
        if not comparison_index or comparison_index not in comparisons:
            return
        urls = self._group_urls(comparisons[comparison_index])
        if urls:
            self._append_unique_urls(urls, comparison_index)

    async def _save_comparison_data(
        self, comparison_data_file: Path, comparisons: ComparisonData
    ) -> None:
        try:
            comparison_json = json.dumps(comparisons, indent=4)
            await asyncio.to_thread(
                comparison_data_file.write_text, comparison_json
            )
            if self.meta.debug:
                logger.debug(
                    f"[cyan]Saved comparison data to {comparison_data_file}"
                )
        except Exception as exc:
            logger.info(f"[yellow]Failed to save comparison data: {exc}")

    async def add_comparison(self) -> ComparisonData | list[ComparisonGroup]:
        comparison_path = self.meta.comparison
        if not isinstance(comparison_path, str):
            return []
        comparison_dir = Path(comparison_path)
        if not comparison_dir.is_dir():
            return []

        comparison_data_file = self._comparison_data_file()
        saved_data = await self._load_saved_comparison(comparison_data_file)
        if saved_data is not None:
            return saved_data

        comparisons = await self._build_comparison_data(comparison_dir)
        self._apply_generated_selection(comparisons)
        self.meta.comparison_groups = comparisons
        await self._save_comparison_data(comparison_data_file, comparisons)
        return comparisons
