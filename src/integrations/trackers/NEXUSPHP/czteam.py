# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

from typing import Any, ClassVar

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class CZTeam(NEXUSPHP):
    """CZTeam is a Romanian private tracker that runs on NexusPHP.

    The category, type, codec and resolution ids are not hard-coded here:
    they are read from ``TRACKERS.CZTEAM`` in the configuration until the
    site ids are confirmed. An unmapped category never reaches the upload
    call.
    """

    tracker = "CZTEAM"
    display_name = "CZTeam"
    base_url = "https://czteam.me"
    source_flag = "[czteam.me] CZTeam"
    torrent_url = f"{base_url}/details.php?id="
    tracker_urls = ("https://czteam.me",)
    supported_categories = ("MOVIE", "TV")
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()

    _ID_MAPPING_KEYS: ClassVar[dict[str, str]] = {
        "category": "categories",
        "type": "types",
        "codec": "codecs",
        "resolution": "resolutions",
    }

    def __init__(self, config: Config) -> None:
        super().__init__(config, "CZTEAM")

    def _configured_ids(self, kind: str) -> dict[str, int]:
        raw = self.tracker_config.get(self._ID_MAPPING_KEYS[kind], {})
        if not isinstance(raw, dict):
            return {}
        values: dict[str, int] = {}
        for key, value in raw.items():
            try:
                mapped = int(value)
            except (TypeError, ValueError):
                continue
            if mapped > 0:
                values[str(key).strip().upper()] = mapped
        return values

    def _mapped_id(self, kind: str, meta_value: object) -> int:
        key = str(meta_value or "").strip().upper()
        if not key:
            return 0
        return self._configured_ids(kind).get(key, 0)

    def get_category(self, meta: Meta) -> int:
        return self._mapped_id("category", meta.category)

    def get_type(self, meta: Meta) -> int:
        return self._mapped_id("type", meta.type)

    def get_codec(self, meta: Meta) -> int:
        return self._mapped_id("codec", meta.video_codec)

    def get_resolution(self, meta: Meta) -> int:
        return self._mapped_id("resolution", meta.resolution)

    async def upload(self, meta: Meta) -> bool:
        if self.get_category(meta) <= 0:
            logger.info(
                f"{self.tracker}: [bold red]The category '{meta.category}' has no "
                "configured id for this tracker; skipping upload.[/bold red]"
            )
            meta.skipping = self.tracker
            return False
        return await super().upload(meta)
