"""Canonical image-host API contracts used by upload and configuration code.

The documentation URLs intentionally point at provider-owned documentation when
one is publicly available.  Hosts without a public API document are marked as
private/custom contracts so callers do not silently assume Chevereto/ImgBB
semantics from a similar-looking endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_IMAGE_HOST_SLOTS = 10


@dataclass(frozen=True)
class ImageHostSpec:
    """Stable metadata for one supported image host integration."""

    key: str
    protocol: str
    config_keys: tuple[str, ...] = ()
    upload_url: str | None = None
    documentation_url: str | None = None
    max_file_bytes: int | None = None
    public_api: bool = True


IMAGE_HOST_SPECS: dict[str, ImageHostSpec] = {
    "imgbb": ImageHostSpec(
        key="imgbb",
        protocol="imgbb-v1",
        config_keys=("imgbb_api",),
        upload_url="https://api.imgbb.com/1/upload",
        documentation_url="https://api.imgbb.com/",
        max_file_bytes=32_000_000,
    ),
    "imgbox": ImageHostSpec(
        key="imgbox",
        protocol="imgbox-web-uploader",
        upload_url="https://imgbox.com/upload/process",
        documentation_url="https://imgbox.com/",
        max_file_bytes=10_000_000,
        public_api=False,
    ),
    "pixhost": ImageHostSpec(
        key="pixhost",
        protocol="pixhost-v2",
        upload_url="https://api.pixhost.to/images",
        documentation_url="https://pixhost.to/api/index.html",
        max_file_bytes=10_000_000,
    ),
    "lensdump": ImageHostSpec(
        key="lensdump",
        protocol="chevereto-v1",
        config_keys=("lensdump_api",),
        upload_url="https://lensdump.com/api/1/upload",
        documentation_url="https://lensdump.com/page/api-doc",
    ),
    "ptscreens": ImageHostSpec(
        key="ptscreens",
        protocol="chevereto-v1",
        config_keys=("ptscreens_api",),
        upload_url="https://ptscreens.com/api/1/upload",
        documentation_url="https://ptscreens.com/api-v1",
    ),
    "onlyimage": ImageHostSpec(
        key="onlyimage",
        protocol="chevereto-v1",
        config_keys=("onlyimage_api",),
        upload_url="https://onlyimage.org/api/1/upload",
        documentation_url="https://onlyimage.org/api-v1",
    ),
    "utppm": ImageHostSpec(
        key="utppm",
        protocol="chevereto-v1",
        config_keys=("utppm_api",),
        upload_url="https://utp.pm/api/1/upload",
        documentation_url="https://v4-docs.chevereto.com/api/1/file-upload.html",
    ),
    "passtheimage": ImageHostSpec(
        key="passtheimage",
        protocol="chevereto-v1",
        config_keys=("passtheima_ge_api",),
        upload_url="https://passtheima.ge/api/1/upload",
        documentation_url="https://v4-docs.chevereto.com/api/1/file-upload.html",
    ),
    "dalexni": ImageHostSpec(
        key="dalexni",
        protocol="imgbb-compatible-private",
        config_keys=("dalexni_api",),
        upload_url="https://dalexni.com/1/upload",
        documentation_url="https://dalexni.com/",
        public_api=False,
    ),
    "zipline": ImageHostSpec(
        key="zipline",
        protocol="zipline",
        config_keys=("zipline_url", "zipline_api_key"),
        documentation_url="https://zipline.diced.sh/docs/api",
    ),
    "midnightscene": ImageHostSpec(
        key="midnightscene",
        protocol="zipline",
        config_keys=("midnightscene_api_key",),
        upload_url="https://img.midnightscene.cc/api/upload",
        documentation_url="https://zipline.diced.sh/docs/api",
    ),
    "seedpool_cdn": ImageHostSpec(
        key="seedpool_cdn",
        protocol="seedpool-private",
        config_keys=("seedpool_cdn_api",),
        upload_url="https://i.seedpool.org/upload",
        documentation_url="https://i.seedpool.org/",
        public_api=False,
    ),
    "sharex": ImageHostSpec(
        key="sharex",
        protocol="sharex-custom",
        config_keys=("sharex_url", "sharex_api_key"),
        documentation_url="https://img.digitalcore.club/",
        public_api=False,
    ),
    "lostimg": ImageHostSpec(
        key="lostimg",
        protocol="lostimg-private",
        config_keys=("lostimg_api",),
        upload_url="https://lostimg.cc/api/v1/images",
        documentation_url="https://lostimg.cc/docs/api",
        public_api=False,
    ),
}


def image_host_max_file_bytes(host: str) -> int | None:
    """Return the documented maximum upload size for a host when known."""
    spec = IMAGE_HOST_SPECS.get(host.lower())
    return spec.max_file_bytes if spec else None


def image_host_size_within_limit(host: str, size: int) -> bool:
    """Check a file size against the provider's documented maximum, if any."""
    maximum = image_host_max_file_bytes(host)
    return maximum is None or size <= maximum


def image_host_config_map() -> dict[str, str | list[str] | None]:
    """Return the config-generator mapping derived from the canonical registry."""
    result: dict[str, str | list[str] | None] = {}
    for key, spec in IMAGE_HOST_SPECS.items():
        if not spec.config_keys:
            result[key] = None
        elif len(spec.config_keys) == 1:
            result[key] = spec.config_keys[0]
        else:
            result[key] = list(spec.config_keys)
    return result
