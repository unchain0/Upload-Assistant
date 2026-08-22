"""Shared artwork validation and explicit-artwork preparation helpers."""

import asyncio
import contextlib
import ipaddress
import re
import socket
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import artwork_dir
from src.integrations.observability.runtime_support import logger

_SUPPORTED_COVER_FORMATS = {"GIF", "JPEG", "PNG", "WEBP"}
# File extensions worth opening at all when scanning a directory for artwork.
_COVER_SUFFIXES = {".gif", ".jpg", ".jpeg", ".png", ".webp"}
MAX_ARTWORK_BYTES = 10 * 1024 * 1024
MAX_ARTWORK_PIXELS = 40_000_000
_POSTER_KEYWORDS = ("poster", "cover", "front", "folder", "artwork", "capa")
_BANNER_KEYWORDS = ("banner", "backdrop", "landscape", "header")


def _http_hostname(value: str | None) -> str | None:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    return parsed.hostname


def _resolved_addresses(hostname: str) -> set[str]:
    return {
        str(result[4][0])
        for result in socket.getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        )
    }


def _addresses_are_public(addresses: set[str]) -> bool:
    return bool(addresses) and all(
        ipaddress.ip_address(address).is_global for address in addresses
    )


def is_public_http_url(value: str | None) -> bool:
    """Return whether an HTTP(S) URL resolves exclusively to public IPs."""
    hostname = _http_hostname(value)
    if hostname is None:
        return False
    try:
        return _addresses_are_public(_resolved_addresses(hostname))
    except OSError, ValueError:
        return False


def _image_metadata_supported(image: Image.Image) -> bool:
    if image.format not in _SUPPORTED_COVER_FORMATS:
        return False
    if image.width <= 0 or image.height <= 0:
        return False
    return image.width * image.height <= MAX_ARTWORK_PIXELS


def _verify_image_bytes(image_bytes: bytes) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(image_bytes)) as image:
            if not _image_metadata_supported(image):
                return False
            image.verify()
    return True


def is_valid_image_bytes(image_bytes: bytes) -> bool:
    """Return whether bytes contain a decodable, non-empty supported image."""
    if not image_bytes:
        return False
    try:
        return _verify_image_bytes(image_bytes)
    except (
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return False


def _cover_size_allowed(size: int) -> bool:
    return 0 < size <= MAX_ARTWORK_BYTES


def _cover_file_bytes(image_path: Path) -> bytes | None:
    if not image_path.is_file():
        return None
    if not _cover_size_allowed(image_path.stat().st_size):
        return None
    return image_path.read_bytes()


def is_valid_cover_image(path: str | Path | None) -> bool:
    """Return whether *path* is a decodable cover image accepted by uploads."""
    if not path:
        return False
    try:
        image_bytes = _cover_file_bytes(Path(path))
    except OSError:
        return False
    return bool(image_bytes is not None and is_valid_image_bytes(image_bytes))


def audiobook_cover_missing_or_invalid(meta: Meta) -> bool:
    """Return whether an audiobook lacks a usable prepared cover artifact."""
    return bool(meta.audiobook and not is_valid_cover_image(meta.artwork_path))


def _artwork_directory(media_path: str) -> Path | None:
    if not media_path:
        return None
    path = Path(media_path).expanduser()
    directory = path if path.is_dir() else path.parent
    return directory if directory.is_dir() else None


def _valid_local_artwork_candidate(candidate: Path) -> bool:
    if candidate.suffix.casefold() not in _COVER_SUFFIXES:
        return False
    if not candidate.is_file():
        return False
    return is_valid_cover_image(candidate)


def _artwork_words(candidate: Path) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", candidate.stem.casefold()))


def _keyword_priority(
    words: set[str], keywords: tuple[str, ...]
) -> int | None:
    matches = [
        keywords.index(keyword) for keyword in words if keyword in keywords
    ]
    return min(matches) if matches else None


def _candidate_rank(
    candidate: Path, words: set[str], keywords: tuple[str, ...]
) -> tuple[int, int, str] | None:
    priority = _keyword_priority(words, keywords)
    if priority is None:
        return None
    exact_name = int(candidate.stem.casefold() not in keywords)
    return priority, exact_name, candidate.name.casefold()


def _candidate_artwork_match(
    candidate: Path,
) -> tuple[str, tuple[int, int, str], Path] | None:
    words = _artwork_words(candidate)
    banner_rank = _candidate_rank(candidate, words, _BANNER_KEYWORDS)
    if banner_rank is not None:
        return "banner", banner_rank, candidate
    poster_rank = _candidate_rank(candidate, words, _POSTER_KEYWORDS)
    if poster_rank is not None:
        return "poster", poster_rank, candidate
    return None


def _best_artwork_sources(
    candidates: dict[str, list[tuple[tuple[int, int, str], Path]]],
) -> dict[str, Path]:
    return {
        kind: min(matches, key=lambda item: item[0])[1]
        for kind, matches in candidates.items()
        if matches
    }


def _find_local_artwork_sources(media_path: str) -> dict[str, Path]:
    """Find the best poster and banner image beside the media being uploaded."""
    directory = _artwork_directory(media_path)
    if directory is None:
        return {}
    candidates: dict[str, list[tuple[tuple[int, int, str], Path]]] = {
        "poster": [],
        "banner": [],
    }
    for candidate in directory.iterdir():
        if not _valid_local_artwork_candidate(candidate):
            continue
        match = _candidate_artwork_match(candidate)
        if match is not None:
            kind, rank, path = match
            candidates[kind].append((rank, path))
    return _best_artwork_sources(candidates)


def _response_content_supported(response: httpx.Response) -> bool:
    if response.status_code != 200:
        return False
    content_length = response.headers.get("Content-Length")
    if not content_length:
        return True
    return (
        content_length.isdigit() and int(content_length) <= MAX_ARTWORK_BYTES
    )


def _redirect_target(response: httpx.Response) -> str | None:
    location = response.headers.get("Location")
    if not location:
        return None
    return str(response.url.join(location))


async def _read_artwork_response(response: httpx.Response) -> bytes | None:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > MAX_ARTWORK_BYTES:
            logger.warning(
                "[yellow]Artwork download exceeds the 10 MiB limit; ignoring it.[/yellow]"
            )
            return None
    payload = bytes(content)
    if not is_valid_image_bytes(payload):
        logger.warning(
            "[yellow]Artwork URL did not return a supported image; ignoring it.[/yellow]"
        )
        return None
    return payload


async def _download_step(
    client: httpx.AsyncClient, current_url: str
) -> tuple[str | None, bytes | None]:
    async with client.stream("GET", current_url) as response:
        if response.is_redirect:
            return _redirect_target(response), None
        if not _response_content_supported(response):
            logger.warning(
                "[yellow]Artwork URL did not return a supported image; ignoring it.[/yellow]"
            )
            return None, None
        return None, await _read_artwork_response(response)


def _public_artwork_url(url: str) -> bool:
    if is_public_http_url(url):
        return True
    logger.warning(
        "[yellow]Artwork URL is not a public HTTP(S) URL; ignoring it.[/yellow]"
    )
    return False


async def _download_with_client(
    client: httpx.AsyncClient, url: str
) -> bytes | None:
    current_url = url
    for _ in range(4):
        if not _public_artwork_url(current_url):
            return None
        next_url, payload = await _download_step(client, current_url)
        if payload is not None:
            return payload
        if next_url is None:
            return None
        current_url = next_url
    return None


async def _download_public_image(url: str) -> bytes | None:
    """Download an explicit image without following redirects to private hosts."""
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=False, trust_env=False
        ) as client:
            return await _download_with_client(client, url)
    except httpx.HTTPError as error:
        logger.warning(f"[yellow]Unable to download artwork: {error}[/yellow]")
        return None


def _png_image_source(source: Path | bytes) -> Path | BytesIO | None:
    if isinstance(source, bytes):
        return BytesIO(source) if _cover_size_allowed(len(source)) else None
    return source if _cover_size_allowed(source.stat().st_size) else None


def _normalized_image_mode(image: Image.Image) -> Image.Image:
    if image.mode in {"RGB", "RGBA"}:
        return image
    target_mode = "RGBA" if "transparency" in image.info else "RGB"
    return image.convert(target_mode)


def _encode_png(
    image_source: Path | BytesIO, destination: Path, temporary: Path
) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(image_source) as image:
            if not _image_metadata_supported(image):
                return False
            image.load()
            image = _normalized_image_mode(image)
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(temporary, "PNG")
    return True


def _cleanup_temporary_artwork(temporary: Path) -> None:
    with contextlib.suppress(OSError):
        temporary.unlink(missing_ok=True)


def _write_png(source: Path | bytes, destination: Path) -> bool:
    """Validate and re-encode artwork into the canonical PNG artifact."""
    temporary = destination.with_suffix(".tmp")
    try:
        image_source = _png_image_source(source)
        if image_source is None:
            return False
        if not _encode_png(image_source, destination, temporary):
            return False
        temporary.replace(destination)
        return True
    except (
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        _cleanup_temporary_artwork(temporary)
        return False


def _artwork_specs() -> tuple[tuple[str, str, str, str], ...]:
    return (
        ("explicit_poster", "POSTER.png", "artwork_path", "poster"),
        (
            "explicit_banner",
            "POSTER_BANNER.png",
            "artwork_banner_path",
            "banner",
        ),
    )


def _current_artwork_path(meta: Meta, meta_name: str) -> Path | None:
    current_path = Path(str(getattr(meta, meta_name) or ""))
    return current_path if is_valid_cover_image(current_path) else None


def _preferred_artwork_path(
    meta: Meta,
    value: str,
    kind: str,
    meta_name: str,
    local_sources: dict[str, Path],
) -> tuple[Path | None, bool]:
    source_path = (
        Path(value).expanduser() if value else local_sources.get(kind)
    )
    discovered_local = not value and source_path is not None
    if source_path is None:
        source_path = _current_artwork_path(meta, meta_name)
    return source_path, discovered_local


def _explicit_http_url(value: str) -> bool:
    return bool(value and urlparse(value).scheme in {"http", "https"})


def _existing_artwork_source(source_path: Path | None) -> Path | None:
    if source_path is None or not source_path.is_file():
        return None
    return source_path


def _provider_artwork_url(meta: Meta, kind: str) -> str | None:
    if kind != "poster":
        return None
    return meta.artwork_url if is_public_http_url(meta.artwork_url) else None


def _warn_invalid_artwork_source(input_name: str, value: str) -> None:
    if not value:
        return
    logger.warning(
        f"[yellow]{input_name.replace('_', ' ')} must be an existing "
        "image file or public HTTP(S) URL; ignoring it.[/yellow]"
    )


async def _resolve_artwork_source(
    meta: Meta,
    input_name: str,
    kind: str,
    value: str,
    source_path: Path | None,
) -> tuple[Path | bytes | None, bool]:
    existing_source = _existing_artwork_source(source_path)
    if existing_source is not None:
        return existing_source, True
    if _explicit_http_url(value):
        return await _download_public_image(value), True
    provider_url = _provider_artwork_url(meta, kind)
    if provider_url is not None:
        return await _download_public_image(provider_url), True
    _warn_invalid_artwork_source(input_name, value)
    return None, False


def _prepared_artwork_warning(input_name: str) -> None:
    logger.warning(
        f"[yellow]Could not prepare {input_name.replace('_', ' ')}; "
        "ignoring it.[/yellow]"
    )


def _persist_prepared_artwork(
    meta: Meta,
    input_name: str,
    meta_name: str,
    destination: Path,
    value: str,
    discovered_local: bool,
    source: Path | bytes,
) -> None:
    setattr(meta, meta_name, str(destination))
    if input_name != "explicit_poster":
        return
    if value:
        meta.artwork_url = value if isinstance(source, bytes) else ""
        return
    if discovered_local:
        meta.artwork_url = ""


async def _prepare_artwork_item(
    meta: Meta,
    output_dir: Path,
    local_sources: dict[str, Path],
    input_name: str,
    output_name: str,
    meta_name: str,
    kind: str,
) -> None:
    value = str(getattr(meta, input_name) or "").strip()
    source_path, discovered_local = _preferred_artwork_path(
        meta, value, kind, meta_name, local_sources
    )
    source, should_prepare = await _resolve_artwork_source(
        meta, input_name, kind, value, source_path
    )
    if not should_prepare:
        return
    destination = output_dir / output_name
    if source is None or not await asyncio.to_thread(
        _write_png, source, destination
    ):
        _prepared_artwork_warning(input_name)
        return
    _persist_prepared_artwork(
        meta,
        input_name,
        meta_name,
        destination,
        value,
        discovered_local,
        source,
    )


async def prepare_artwork(meta: Meta) -> None:
    """Resolve every artwork source into the canonical per-release PNG files.

    Explicit arguments win over local files beside the media; those in turn win
    over category-specific extraction and remote metadata providers.
    """
    output_dir = artwork_dir(meta.base_dir, meta.uuid)
    local_sources = await asyncio.to_thread(
        _find_local_artwork_sources, str(meta.path or "")
    )
    for input_name, output_name, meta_name, kind in _artwork_specs():
        await _prepare_artwork_item(
            meta,
            output_dir,
            local_sources,
            input_name,
            output_name,
            meta_name,
            kind,
        )
