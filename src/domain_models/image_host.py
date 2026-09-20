"""Domain vocabulary for supported image-host identities and configuration."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

MAX_IMAGE_HOST_SLOTS: Final = 10

IMAGE_HOST_CONFIG_KEYS = MappingProxyType(
    {
        "imgbb": ("imgbb_api",),
        "imgbox": (),
        "pixhost": (),
        "lensdump": ("lensdump_api",),
        "ptscreens": ("ptscreens_api",),
        "onlyimage": ("onlyimage_api",),
        "utppm": ("utppm_api",),
        "passtheimage": ("passtheima_ge_api",),
        "dalexni": ("dalexni_api",),
        "zipline": ("zipline_url", "zipline_api_key"),
        "midnightscene": ("midnightscene_api_key",),
        "seedpool_cdn": ("seedpool_cdn_api",),
        "sharex": ("sharex_url", "sharex_api_key"),
        "lostimg": ("lostimg_api",),
    }
)

IMAGE_HOST_NAMES: Final[tuple[str, ...]] = tuple(IMAGE_HOST_CONFIG_KEYS)
