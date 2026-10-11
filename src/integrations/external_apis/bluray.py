# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import random
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import cli_ui
import httpx
from bs4 import BeautifulSoup
from bs4.element import AttributeValueList
from rich.console import Console

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger

console = Console()

Release = MutableMapping[str, Any]
MovieLink = MutableMapping[str, Any]


def _style_contains(style: str | None, token: str) -> bool:
    return bool(style and token in style)


def _style_green(style: str | None) -> bool:
    return _style_contains(style, "color: green")


def _style_gray(style: str | None) -> bool:
    return _style_contains(style, "color: #999999")


def _style_specs(style: str | None) -> bool:
    return _style_contains(style, "font-size: 12px")


def _bluray_search_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.blu-ray.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def _bluray_search_cache_path(meta: Meta, imdb_id: str) -> Path:
    return (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / f"debug_bluray_search_{imdb_id}.html"
    )


async def _read_valid_bluray_cache(path: Path, label: str) -> str | None:
    if not path.exists():
        return None
    try:
        logger.debug(f"[green]Found existing file for {label}[/green]")
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except Exception as error:
        logger.info(f"[yellow]Error reading cached file: {error!s}[/yellow]")
        return None
    if text and "No index" not in text:
        return text
    logger.info(
        "[yellow]Cached file exists but appears to be invalid, will fetch fresh data[/yellow]"
    )
    return None


async def _save_bluray_debug(path: Path, text: str, message: str) -> None:
    try:
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
        logger.debug(message)
    except Exception as error:
        logger.info(f"[dim]Could not save debug file: {error!s}[/dim]")


def _bluray_search_response_kind(response: Any) -> str:
    if response.status_code == 200 and "No index" not in response.text:
        return "success"
    return "blocked" if "No index" in response.text else "status"


def _log_bluray_search_failure(
    kind: str, response: Any, attempt: int, total: int
) -> None:
    if kind == "blocked":
        logger.info(
            f"[red]Blocked by blu-ray.com (Anti-scraping protection) (attempt {attempt}/{total})[/red]"
        )
        logger.info(f"[dim]Response preview: {response.text[:150]}...[/dim]")
        return
    logger.error(
        f"[red]Failed with status code: {response.status_code} (attempt {attempt}/{total})[/red]"
    )


async def _bluray_search_attempt(
    url: str, headers: dict[str, str], attempt: int, total: int
) -> Any | None:
    delay = random.uniform(1, 3)  # nosec B311 - rate limiting only  # noqa: S311
    logger.debug(
        f"[dim]Waiting {delay:.2f} seconds before request (attempt {attempt}/{total})...[/dim]"
    )
    await asyncio.sleep(delay)
    logger.debug(
        f"[yellow]Sending request to blu-ray.com (attempt {attempt}/{total})...[/yellow]"
    )
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True
        ) as client:
            return await client.get(url, headers=headers)
    except httpx.RequestError as error:
        logger.info(
            f"[red]HTTP request error when accessing {url} (attempt {attempt}/{total}): {error!s}[/red]"
        )
        return None


async def _bluray_retry_pause(backoff: float, *, blocked: bool) -> float:
    next_backoff = backoff * 2
    log = logger.info if blocked else logger.debug
    log(f"[yellow]Retrying in {next_backoff:.1f} seconds...[/yellow]")
    await asyncio.sleep(next_backoff)
    return next_backoff


async def _fetch_bluray_search_html(
    url: str, headers: dict[str, str]
) -> str | None:
    total = 3
    backoff = 3.0
    for index in range(total):
        attempt = index + 1
        response = await _bluray_search_attempt(url, headers, attempt, total)
        if response is not None:
            kind = _bluray_search_response_kind(response)
            if kind == "success":
                return str(response.text)
            _log_bluray_search_failure(kind, response, attempt, total)
        else:
            kind = "request"
        if attempt == total:
            logger.info(
                "[red]Maximum retries reached, giving up on search[/red]"
            )
            break
        backoff = await _bluray_retry_pause(backoff, blocked=kind == "blocked")
    return None


async def search_bluray(meta: Meta) -> str | None:
    imdb_id = f"tt{(meta.imdb_id or 0):07d}"
    url = f"https://www.blu-ray.com/search/?quicksearch=1&quicksearch_country=all&quicksearch_keyword={imdb_id}&section=theatrical"
    cache_path = _bluray_search_cache_path(meta, imdb_id)
    cached = await _read_valid_bluray_cache(cache_path, imdb_id)
    if cached is not None:
        return cached
    logger.debug(f"[dim]Search URL: {url}[/dim]")
    response_text = await _fetch_bluray_search_html(
        url, _bluray_search_headers()
    )
    if response_text is None:
        logger.error(
            "[red]Failed to retrieve search results after all attempts[/red]"
        )
        return None
    await _save_bluray_debug(
        cache_path,
        response_text,
        f"[dim]Saved search response to debug_bluray_search_{imdb_id}.html[/dim]",
    )
    return response_text


def _movie_caption_text(movie_div: Any, selector: str, fallback: str) -> str:
    element: Any = movie_div.select_one(selector)
    return element.text.strip() if element else fallback


def _movie_link_from_div(movie_div: Any) -> MovieLink | None:
    link: Any = movie_div.find("a", class_="alphaborder")
    if not link or "href" not in link.attrs:
        logger.info("[red]Movie div doesn't have a valid link[/red]")
        return None
    releases_url = f"{link['href']}#Releases"
    title = _movie_caption_text(
        movie_div,
        'div.figurecaptionbottom div[style*="font-weight: bold"]',
        "Unknown Title",
    )
    year = _movie_caption_text(
        movie_div,
        'div.figurecaptionbottom div[style*="margin-top"]',
        "Unknown Year",
    )
    logger.info(f"[green]Found movie: {title} ({year})[/green]")
    logger.info(f"[dim]URL: {releases_url}[/dim]")
    return {"title": title, "year": year, "releases_url": releases_url}


def _movie_links_from_divs(movie_divs: list[Any]) -> list[MovieLink]:
    results: list[MovieLink] = []
    for movie_div in movie_divs:
        result = _movie_link_from_div(movie_div)
        if result is not None:
            results.append(result)
    return results


def extract_bluray_links(html_content: str | None) -> list[MovieLink] | None:
    if not html_content:
        logger.info("[red]No HTML content to extract links from[/red]")
        return None
    try:
        soup: Any = BeautifulSoup(html_content, "lxml")
        movie_divs: list[Any] = list(soup.select("div.figure"))
    except Exception as error:
        logger.error(f"[red]Error parsing HTML: {error!s}[/red]")
        console.print_exception()
        return None
    if not movie_divs:
        return None
    return _movie_links_from_divs(movie_divs)


def _release_type_message(is_3d: bool, is_4k: bool, is_dvd: bool) -> str:
    if is_3d:
        return "[blue]Looking for 3D Blu-ray releases[/blue]"
    if is_4k:
        return "[blue]Looking for 4K/UHD Blu-ray releases[/blue]"
    if is_dvd:
        return "[blue]Looking for DVD releases[/blue]"
    return "[blue]Looking for standard Blu-ray releases[/blue]"


def _release_debug_path(
    meta: Meta, release_type: str, product_id: str
) -> Path:
    return (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / f"debug_bluray_{release_type}_{product_id}.html"
    )


async def _save_release_search_debug(
    meta: Meta, release_type: str, product_id: str, html_content: str
) -> None:
    path = _release_debug_path(meta, release_type, product_id)
    await _save_bluray_debug(
        path,
        html_content,
        f"[dim]Saved releases response to debug_bluray_{release_type}_{product_id}.html[/dim]",
    )


def _selected_release_sections(soup: Any, is_dvd: bool) -> list[Any]:
    pattern = (
        r"DVD Editions"
        if is_dvd
        else r"Blu-ray Editions|4K Blu-ray Editions|3D Blu-ray Editions"
    )
    return list(soup.find_all("h3", string=re.compile(pattern)))


def _standard_bluray_section(title: str) -> bool:
    return (
        "Blu-ray Editions" in title
        and "3D Blu-ray Editions" not in title
        and "4K Blu-ray Editions" not in title
    )


def _section_matches_release_type(
    title: str, *, is_3d: bool, is_4k: bool, is_dvd: bool
) -> bool:
    if is_3d:
        return "3D Blu-ray Editions" in title
    if is_4k:
        return "4K Blu-ray Editions" in title
    if is_dvd:
        return "DVD Editions" in title
    return _standard_bluray_section(title)


def _release_section_label(*, is_3d: bool, is_4k: bool, is_dvd: bool) -> str:
    if is_3d:
        return "3D"
    if is_4k:
        return "4K"
    if is_dvd:
        return "DVD"
    return "standard Blu-ray"


def _filtered_release_sections(
    sections: list[Any], *, is_3d: bool, is_4k: bool, is_dvd: bool
) -> list[Any]:
    filtered: list[Any] = []
    label = _release_section_label(is_3d=is_3d, is_4k=is_4k, is_dvd=is_dvd)
    for section in sections:
        title = str(section.text)
        if not _section_matches_release_type(
            title, is_3d=is_3d, is_4k=is_4k, is_dvd=is_dvd
        ):
            continue
        filtered.append(section)
        logger.debug(f"[green]Including {label} section: {title}[/green]")
    return filtered


def _valid_release_anchor(node: Any) -> bool:
    if getattr(node, "name", None) != "a" or not node.has_attr("href"):
        return False
    href = str(node["href"])
    return "blu-ray.com/movies/" in href or "blu-ray.com/dvd/" in href


def _release_links_in_section(
    section: Any, release_type_debug: str
) -> list[Any]:
    if not section.find_parent("tr"):
        logger.info(
            f"[red]Could not find parent tr for {release_type_debug} section[/red]"
        )
        return []
    links: list[Any] = []
    current: Any = section.find_next()
    while current and getattr(current, "name", None) != "h3":
        if _valid_release_anchor(current):
            links.append(current)
        current = current.find_next()
    return links


def _tag_text_or_unknown(tag: Any) -> str:
    return tag.text.strip() if tag else "Unknown"


def _release_id_from_url(url: str) -> str | None:
    match = re.search(r"blu-ray\.com/(movies|dvd)/.*?/(\d+)/", url)
    return match.group(2) if match else None


def _release_from_link(link: Any) -> Release | None:
    try:
        release_url = str(link["href"])
        release_id = _release_id_from_url(release_url)
        if not release_id:
            logger.info(
                f"[red]Could not extract release ID from URL: {release_url}[/red]"
            )
            return None
        title = str(link.get("title", link.text.strip()))
        country_flag: Any = link.find_previous("img", width="18", height="12")
        country = (
            str(country_flag.get("title", "Unknown"))
            if country_flag
            else "Unknown"
        )
        price = _tag_text_or_unknown(
            link.find_next("small", style=_style_green)
        )
        publisher = _tag_text_or_unknown(
            link.find_next("small", style=_style_gray)
        )
        logger.debug(f"[green]Found release ID: {release_id}[/green]")
        return {
            "title": title,
            "url": release_url,
            "price": price,
            "publisher": publisher,
            "country": country,
            "release_id": release_id,
        }
    except Exception as error:
        logger.error(f"[red]Error processing release: {error!s}[/red]")
        console.print_exception()
        return None


def _releases_from_sections(
    sections: list[Any], release_type_debug: str
) -> list[Release]:
    releases: list[Release] = []
    for section in sections:
        for link in _release_links_in_section(section, release_type_debug):
            release = _release_from_link(link)
            if release is not None:
                releases.append(release)
    return releases


async def extract_bluray_release_info(
    html_content: str, meta: Meta, product_id: str
) -> list[Release]:
    if not html_content:
        logger.info("[red]No HTML content to extract release info from[/red]")
        return []
    release_type, is_3d, is_4k, is_dvd = _derive_release_type(meta)
    release_type_debug = "DVD" if is_dvd else "Blu-ray"
    logger.info(_release_type_message(is_3d, is_4k, is_dvd))
    await _save_release_search_debug(
        meta, release_type, product_id, html_content
    )
    try:
        soup: Any = BeautifulSoup(html_content, "lxml")
        selected = _selected_release_sections(soup, is_dvd)
        logger.debug(
            f"[blue]Found {len(selected)} {release_type_debug} section(s)[/blue]"
        )
        filtered = _filtered_release_sections(
            selected, is_3d=is_3d, is_4k=is_4k, is_dvd=is_dvd
        )
        releases = _releases_from_sections(filtered, release_type_debug)
    except Exception as error:
        logger.error(
            f"[red]Error parsing Blu-ray release HTML: {error!s}[/red]"
        )
        console.print_exception()
        return []
    logger.info(
        f"[green]Found {len(releases)} potential matching releases[/green]"
    )
    return releases


async def extract_product_id(url: str) -> str | None:
    pattern = r"blu-ray\.com/.*?/(\d+)/"
    match = re.search(pattern, url)

    if match:
        product_id = match.group(1)
        logger.debug(
            f"[green]Successfully extracted product ID: {product_id}[/green]"
        )
        return product_id

    logger.info(f"[red]Could not extract product ID from URL: {url}[/red]")
    return None


def _derive_release_type(meta: Meta) -> tuple[str, bool, bool, bool]:
    is_3d = meta.three_d.lower() == "yes"
    resolution = meta.resolution.lower()
    is_4k = "2160p" in resolution or "4k" in resolution
    is_dvd = str(meta.is_disc).upper() == "DVD"
    release_type = (
        "4K" if is_4k else "3D" if is_3d else "DVD" if is_dvd else "BD"
    )
    return release_type, is_3d, is_4k, is_dvd


def _bluray_release_headers(releases_url: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": releases_url,
        "X-Requested-With": "XMLHttpRequest",
    }


def _bluray_release_cache_path(meta: Meta, product_id: str) -> Path:
    release_type, _is_3d, _is_4k, _is_dvd = _derive_release_type(meta)
    return (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / f"debug_bluray_{release_type}_{product_id}.html"
    )


def _annotate_movie_releases(
    releases: list[Release], movie: Mapping[str, Any]
) -> list[Release]:
    for release in releases:
        release["movie_title"] = movie["title"]
        release["movie_year"] = movie["year"]
    return releases


async def _cached_movie_releases(
    meta: Meta, movie: Mapping[str, Any], product_id: str
) -> list[Release] | None:
    cache_path = _bluray_release_cache_path(meta, product_id)
    if not cache_path.exists():
        return None
    try:
        logger.debug(
            f"[green]Found existing release data for product ID {product_id}[/green]"
        )
        response_text = await asyncio.to_thread(
            cache_path.read_text, encoding="utf-8"
        )
    except Exception as error:
        logger.info(f"[yellow]Error reading cached file: {error!s}[/yellow]")
        return None
    if not response_text or "No index" in response_text:
        logger.info(
            "[yellow]Cached file exists but appears to be invalid, will fetch fresh data[/yellow]"
        )
        return None
    releases = await extract_bluray_release_info(
        response_text, meta, product_id
    )
    return _annotate_movie_releases(releases, movie)


async def _release_ajax_attempt(
    ajax_url: str,
    headers: dict[str, str],
    attempt: int,
    total: int,
) -> Any | None:
    try:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True
        ) as client:
            return await client.get(ajax_url, headers=headers)
    except httpx.RequestError as error:
        logger.info(
            f"[red]HTTP request error when accessing {ajax_url} (attempt {attempt}/{total}): {error!s}[/red]"
        )
        return None
    except Exception as error:
        logger.error(
            f"[red]Error fetching release details from {ajax_url}: {error!s}[/red]"
        )
        console.print_exception()
        return None


def _release_ajax_kind(response: Any) -> str:
    if response.status_code == 200 and "No index" not in response.text:
        return "success"
    return "blocked" if "No index" in response.text else "status"


def _log_release_ajax_failure(
    ajax_url: str, response: Any, kind: str, attempt: int, total: int
) -> None:
    if kind == "blocked":
        logger.info(
            f"[red]Blocked by blu-ray.com when accessing {ajax_url} (attempt {attempt}/{total})[/red]"
        )
        return
    logger.error(
        f"[red]Failed to get release information from {ajax_url}, status code: {response.status_code} (attempt {attempt}/{total})[/red]"
    )


async def _fetch_release_ajax_html(
    ajax_url: str, headers: dict[str, str]
) -> str | None:
    delay = random.uniform(2, 4)  # nosec B311 - rate limiting only  # noqa: S311
    logger.debug(f"[dim]Waiting {delay:.2f} seconds before request...[/dim]")
    await asyncio.sleep(delay)
    total = 3
    backoff = 3.0
    for attempt in range(1, total + 1):
        response = await _release_ajax_attempt(
            ajax_url, headers, attempt, total
        )
        if response is not None:
            kind = _release_ajax_kind(response)
            if kind == "success":
                return str(response.text)
            _log_release_ajax_failure(ajax_url, response, kind, attempt, total)
        if attempt < total:
            backoff *= 2
            logger.info(
                f"[yellow]Retrying in {backoff:.1f} seconds...[/yellow]"
            )
            await asyncio.sleep(backoff)
    logger.info("[red]Maximum retries reached, giving up on this URL[/red]")
    return None


async def _movie_releases(
    meta: Meta, movie: Mapping[str, Any]
) -> list[Release]:
    releases_url = str(movie["releases_url"])
    product_id = await extract_product_id(releases_url)
    if not product_id:
        logger.info(
            f"[red]Could not extract product ID from {releases_url}[/red]"
        )
        return []
    cached = await _cached_movie_releases(meta, movie, product_id)
    if cached is not None:
        return cached
    ajax_url = f"https://www.blu-ray.com/products/menu_ajax.php?p={product_id}&c=20&action=showreleasesall"
    logger.info(f"[dim]Releases URL: {ajax_url}[/dim]")
    response_text = await _fetch_release_ajax_html(
        ajax_url, _bluray_release_headers(releases_url)
    )
    if response_text is None:
        return []
    releases = await extract_bluray_release_info(
        response_text, meta, product_id
    )
    logger.info(
        f"[green]Found {len(releases)} matching releases for this movie[/green]"
    )
    return _annotate_movie_releases(releases, movie)


async def _collect_bluray_releases(
    meta: Meta, movie_links: list[MovieLink]
) -> list[Release]:
    matching: list[Release] = []
    for index, movie in enumerate(movie_links, 1):
        logger.debug(
            f"[blue]Processing movie {index}/{len(movie_links)}: {movie['title']} ({movie['year']})[/blue]"
        )
        matching.extend(await _movie_releases(meta, movie))
    return matching


def _interactive_bluray_mode(meta: Meta) -> bool:
    return not meta.unattended or bool(meta.unattended_confirm)


def _log_release_choices(releases: list[Release]) -> None:
    for index, release in enumerate(releases, 1):
        logger.info(
            f"[green]{index}. {release['movie_title']} ({release['movie_year']}):[/green]"
        )
        logger.info(f"   [blue]Title: {release['title']}[/blue]")
        logger.info(f"   [blue]Country: {release['country']}[/blue]")
        logger.info(f"   [blue]Publisher: {release['publisher']}[/blue]")
        logger.info(f"   [blue]Price: {release['price']}[/blue]")
        logger.info(f"   [dim]URL: {release['url']}[/dim]")


def _log_release_selection_prompt() -> None:
    logger.info("")
    logger.info("[green]Release Selection")
    logger.info("[green]=======================================")
    logger.info(
        "[dim]Please select a release to use for region and distributor information:"
    )
    logger.info(
        "[dim]Enter release number, 'a' for all releases, or 'n' to skip"
    )
    logger.info(
        "[dim]Selecting all releases will search every release for more information..."
    )
    logger.info("[dim]More releases will require more time to process")


def _apply_selected_release_metadata(
    meta: Meta, release: Mapping[str, Any]
) -> None:
    region_code = map_country_to_region_code(str(release["country"]))
    meta.region = region_code or ""
    meta.distributor = str(release["publisher"]).upper()
    meta.release_url = str(release["url"])
    cli_ui.info(
        f"Set region code to: {region_code}, distributor to: {str(release['publisher']).upper()}"
    )


async def _enrich_selected_release(meta: Meta, release: Release) -> Release:
    if not meta.use_bluray_images:
        return release
    logger.info(
        "[yellow]Fetching release details to get cover images...[/yellow]"
    )
    detailed = await fetch_release_details(release, meta)
    cover_images = detailed.get("cover_images")
    if cover_images:
        meta.bluray_cover_urls = cover_images
        await download_cover_images(meta)
    return detailed


_RETRY_BLURAY_SELECTION = object()


def _release_selection_index(selection: str, count: int) -> int | None:
    try:
        selected_idx = int(selection)
    except ValueError:
        cli_ui.warning(
            f"Invalid input: '{selection}'. Please enter a number, 'a', or 'n'"
        )
        return None
    if not 1 <= selected_idx <= count:
        cli_ui.warning(
            f"Invalid selection: {selected_idx}. Must be between 1 and {count}"
        )
        return None
    return selected_idx


async def _release_selection_result(
    selection: str, meta: Meta, releases: list[Release]
) -> list[Release] | object:
    if selection == "a":
        cli_ui.info("All releases selected")
        return await process_all_releases(releases, meta)
    if selection == "n":
        cli_ui.info("Skipped - not using Blu-ray.com information")
        return []
    selected_idx = _release_selection_index(selection, len(releases))
    if selected_idx is None:
        return _RETRY_BLURAY_SELECTION
    selected = releases[selected_idx - 1]
    cli_ui.info(
        f"Selected: {selected['title']} - {selected['country']} - {selected['publisher']}"
    )
    _apply_selected_release_metadata(meta, selected)
    return [await _enrich_selected_release(meta, selected)]


async def _select_bluray_release(
    meta: Meta, releases: list[Release]
) -> list[Release]:
    _log_release_choices(releases)
    _log_release_selection_prompt()
    while True:
        try:
            raw = cli_ui.ask_string(f"Selection (1-{len(releases)}/a/n): ")
            selection = (raw or "").strip().lower()
            result = await _release_selection_result(selection, meta, releases)
            if result is not _RETRY_BLURAY_SELECTION:
                return cast(list[Release], result)
        except (KeyboardInterrupt, EOFError) as exc:
            raise OperationAbortedError(
                "Blu-ray release selection was cancelled by the user."
            ) from exc


def _bluray_results_debug_path(meta: Meta, release_count: int) -> Path:
    imdb_id = meta.imdb_id or 0
    return (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / f"bluray_results_tt{imdb_id:07d}_{release_count}releases.json"
    )


def _bluray_results_debug_payload(
    meta: Meta, releases: list[Release]
) -> dict[str, Any]:
    imdb_value = meta.imdb_id if meta.imdb_id is not None else 0
    return {
        "movie": {
            "title": meta.title if meta.title is not None else "Unknown",
            "imdb_id": f"tt{imdb_value:07d}",
        },
        "matching_releases": releases,
    }


async def _save_bluray_results_debug(
    meta: Meta, releases: list[Release]
) -> None:
    path = _bluray_results_debug_path(meta, len(releases))
    try:
        text = json.dumps(
            _bluray_results_debug_payload(meta, releases), indent=2
        )
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
        logger.debug(f"[dim]Saved results to {path}[/dim]")
    except Exception as error:
        logger.info(f"[dim]Could not save debug results: {error!s}[/dim]")


async def _matching_bluray_releases(meta: Meta) -> list[Release]:
    imdb_id_value = meta.imdb_id or 0
    html_content = await search_bluray(meta)
    if not html_content:
        logger.error(
            "[red]Failed to get search results from blu-ray.com[/red]"
        )
        return []
    movie_links = extract_bluray_links(html_content) or []
    if not movie_links:
        logger.debug(
            f"[red]No movies found for IMDB ID: tt{imdb_id_value:07d}[/red]"
        )
        return []
    return await _collect_bluray_releases(meta, movie_links)


async def _resolve_bluray_release_selection(
    meta: Meta, matching_releases: list[Release]
) -> list[Release]:
    if _interactive_bluray_mode(meta):
        return await _select_bluray_release(meta, matching_releases)
    logger.info("[yellow]Unattended mode - selecting all releases")
    return await process_all_releases(matching_releases, meta)


async def get_bluray_releases(meta: Meta) -> list[Release]:
    logger.info("[blue]===== Starting blu-ray.com release search =====[/blue]")
    imdb_id_value = meta.imdb_id or 0
    logger.info(
        f"[blue]Movie: {(meta.uuid if meta.uuid is not None else 'Unknown')}, IMDB ID: tt{imdb_id_value:07d}[/blue]"
    )
    matching_releases = await _matching_bluray_releases(meta)
    logger.info(
        "[yellow]===== BluRay.com search results summary =====[/yellow]"
    )
    if matching_releases:
        return await _resolve_bluray_release_selection(meta, matching_releases)
    await _save_bluray_results_debug(meta, matching_releases)
    return matching_releases


def _empty_release_specs() -> dict[str, Any]:
    return {
        "video": {},
        "audio": [],
        "subtitles": [],
        "discs": {},
        "playback": {},
    }


def _section_value(section: str, pattern: str) -> str:
    match = re.search(pattern, section)
    return match.group(1).strip() if match else ""


def _parse_video_specs(specs_td: Any, specs: dict[str, Any]) -> None:
    section = extract_section(specs_td, "Video")
    if not section:
        return
    video = cast(dict[str, Any], specs["video"])
    codec = _section_value(section, r"Codec: ([^<\n]+)")
    resolution = _section_value(section, r"Resolution: ([^<\n]+)")
    if codec:
        video["codec"] = codec
        logger.debug(f"[blue]Video Codec: {codec}[/blue]")
    if resolution:
        video["resolution"] = resolution
        logger.debug(f"[blue]Resolution: {resolution}[/blue]")


def _specs_div(specs_td: Any, long_id: str, short_id: str) -> Any | None:
    value = specs_td.find("div", id=long_id)
    if value:
        return value
    value = specs_td.find("div", id=short_id)
    if value:
        logger.debug(
            f"[dim]Using {short_id} because {long_id} wasn't found[/dim]"
        )
    return value


def _audio_raw_lines(audio_div: Any) -> list[str]:
    audio_html = re.sub(r"<br\s*/?>", "\n", str(audio_div))
    raw_text = BeautifulSoup(audio_html, "lxml").get_text()
    return [
        line.strip()
        for line in raw_text.split("\n")
        if line.strip() and "less" not in line
    ]


def _audio_line_language(line: str) -> str:
    return line.split(":", 1)[0].strip() if ":" in line else ""


def _same_audio_language(current: str, following: str) -> str:
    language = _audio_line_language(current)
    if not language or language != _audio_line_language(following):
        return ""
    return language


def _current_is_atmos(current: str) -> bool:
    return "atmos" in current.lower() and "Dolby Atmos" in current


def _following_has_dolby_core(following: str) -> bool:
    return "Dolby Digital" in following or "Dolby TrueHD" in following


def _atmos_core_format(current: str, following: str) -> str:
    if not _current_is_atmos(current) or not _following_has_dolby_core(
        following
    ):
        return ""
    return "Dolby TrueHD Atmos" if "TrueHD" in following else "Dolby Atmos"


def _atmos_channel_info(following: str) -> str:
    if "7.1" in following:
        return "7.1"
    return "5.1" if "5.1" in following else ""


def _atmos_core_pair(current: str, following: str) -> str | None:
    language = _same_audio_language(current, following)
    codec = _atmos_core_format(current, following)
    if not language or not codec:
        return None
    return f"{language}: {codec} {_atmos_channel_info(following)}"


def _append_audio_line(audio_lines: list[str], current: str) -> None:
    if current.startswith("Note:"):
        if audio_lines:
            audio_lines[-1] = f"{audio_lines[-1]} - {current}"
        return
    audio_lines.append(current)


def _combined_audio_step(
    raw_lines: list[str], index: int
) -> tuple[str | None, int]:
    current = raw_lines[index]
    if index + 1 >= len(raw_lines):
        return None, 1
    combined = _atmos_core_pair(current, raw_lines[index + 1])
    return (combined, 2) if combined is not None else (None, 1)


def _combine_audio_lines(raw_lines: list[str]) -> list[str]:
    audio_lines: list[str] = []
    index = 0
    while index < len(raw_lines):
        combined, step = _combined_audio_step(raw_lines, index)
        if combined is not None:
            audio_lines.append(combined)
        else:
            _append_audio_line(audio_lines, raw_lines[index])
        index += step
    return audio_lines


def _parse_audio_specs(specs_td: Any, specs: dict[str, Any]) -> None:
    if not extract_section(specs_td, "Audio"):
        return
    audio_div = _specs_div(specs_td, "longaudio", "shortaudio")
    if not audio_div:
        return
    audio_lines = _combine_audio_lines(_audio_raw_lines(audio_div))
    specs["audio"] = audio_lines
    logger.debug(f"[blue]Audio Tracks: {len(audio_lines)} found[/blue]")
    for track in audio_lines:
        logger.debug(f"[dim]  - {track}[/dim]")


def _parse_subtitle_specs(specs_td: Any, specs: dict[str, Any]) -> None:
    if not extract_section(specs_td, "Subtitles"):
        return
    subs_div = _specs_div(specs_td, "longsubs", "shortsubs")
    if not subs_div:
        return
    subtitle_text = subs_div.get_text(separator="\n").strip()
    subtitle_text = re.sub(r"\s*\(less\)\s*", "", subtitle_text)
    subtitles = [
        value.strip()
        for value in re.split(r",|\n", subtitle_text)
        if value.strip()
    ]
    specs["subtitles"] = subtitles
    logger.debug(f"[blue]Subtitles: {', '.join(subtitles)}[/blue]")


def _disc_count_value(value: str) -> int:
    if value.isdigit():
        return int(value)
    return {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}.get(value, 1)


def _disc_format_from_match(match: re.Match[str]) -> str:
    if match.group(1):
        return match.group(1).strip()
    details = match.group(3)
    if not details:
        return "multiple discs"
    format_match = re.search(r"(\d+\s*BD-\d+|\d+\s*BD)", details)
    logger.debug(f"[dim]BD Format Match: {format_match}[/dim]")
    return format_match.group(1).strip() if format_match else "multiple discs"


def _parse_disc_count(section: str, discs: dict[str, Any]) -> None:
    match = re.search(
        r"Single disc \(1 ([^)]+)\)|(One|Two|Three|Four|Five|\d+)[ -]disc set(?:\s*\(([^)]+)\))?",
        section,
    )
    logger.debug(f"[dim]Disc Count Match: {match}[/dim]")
    if not match:
        return
    discs["count"] = 1 if match.group(1) else _disc_count_value(match.group(2))
    discs["format"] = _disc_format_from_match(match)


def _parse_disc_specs(specs_td: Any, specs: dict[str, Any]) -> None:
    section = extract_section(specs_td, "Discs")
    if not section:
        return
    discs = cast(dict[str, Any], specs["discs"])
    disc_type = _section_value(
        section, r"(Blu-ray Disc|DVD|Ultra HD Blu-ray|4K Ultra HD)"
    )
    if disc_type:
        discs["type"] = disc_type
        logger.debug(f"[blue]Disc Type: {disc_type}[/blue]")
    _parse_disc_count(section, discs)


def _playback_region(section: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?:2K Blu-ray|4K Blu-ray|DVD): Region ([A-C])(?: \(([^)]+)\))?",
        section,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip() if match.group(
        2
    ) else ""


def _parse_playback_specs(
    specs_td: Any, specs: dict[str, Any], meta: Meta
) -> None:
    section = extract_section(specs_td, "Playback")
    parsed = _playback_region(section) if section else None
    if parsed is None:
        return
    region, notes = parsed
    playback = cast(dict[str, Any], specs["playback"])
    playback["region"] = region
    playback["region_notes"] = notes
    logger.debug(f"[blue]Region: {region}[/blue]")
    if notes and meta.debug:
        logger.info(f"[dim]Region Notes: {notes}[/dim]")


def _apply_release_cover_images(
    response_text: str, release: Release, meta: Meta
) -> None:
    if not meta.use_bluray_images:
        return
    cover_images = extract_cover_images(response_text)
    if not cover_images:
        return
    release["cover_images"] = cover_images
    logger.debug(f"[green]Found {len(cover_images)} cover images:[/green]")
    for image_type, url in cover_images.items():
        logger.debug(f"[dim]  - {image_type}: {url}[/dim]")


def _parse_release_specs(specs_td: Any, meta: Meta) -> dict[str, Any]:
    specs = _empty_release_specs()
    _parse_video_specs(specs_td, specs)
    _parse_audio_specs(specs_td, specs)
    _parse_subtitle_specs(specs_td, specs)
    _parse_disc_specs(specs_td, specs)
    _parse_playback_specs(specs_td, specs, meta)
    return specs


async def parse_release_details(
    response_text: str, release: Release, meta: Meta
) -> Release:
    try:
        soup: Any = BeautifulSoup(response_text, "lxml")
        specs_td: Any = soup.find("td", width="228px", style=_style_specs)
        if not specs_td:
            logger.info(
                "[red]Could not find specs section on the release page[/red]"
            )
            return release
        release["specs"] = _parse_release_specs(specs_td, meta)
        _apply_release_cover_images(response_text, release, meta)
        logger.debug(
            f"[green]Successfully parsed details for {release['title']}[/green]"
        )
        return release
    except Exception as error:
        logger.error(f"[red]Error parsing release details: {error!s}[/red]")
        console.print_exception()
        return release


def _covers_json_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid / "covers.json"


def _covers_match_release(existing_covers: Any, release_url: Any) -> bool:
    if not isinstance(existing_covers, list):
        return False
    for cover in cast(list[Any], existing_covers):
        if not isinstance(cover, Mapping):
            continue
        cover_map = cast(Mapping[str, Any], cover)
        if cover_map.get("release_url") == release_url:
            return True
    return False


def _delete_covers_json(path: Path, *, corrupted: bool) -> None:
    try:
        path.unlink()
        message = (
            "[yellow]Deleted potentially corrupted covers.json file[/yellow]"
            if corrupted
            else "[yellow]Deleting outdated covers.json file[/yellow]"
        )
        logger.debug(message)
    except Exception as error:
        if corrupted:
            logger.error(
                f"[red]Failed to delete corrupted covers.json: {error!s}[/red]"
            )
        else:
            logger.error(
                f"[red]Failed to delete outdated covers.json: {error!s}[/red]"
            )


async def _reuse_existing_covers(meta: Meta, path: Path) -> bool:
    if not path.exists():
        return False
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        existing_covers = json.loads(text)
    except Exception as error:
        logger.error(f"[red]Error reading covers.json: {error!s}[/red]")
        _delete_covers_json(path, corrupted=True)
        return False
    if _covers_match_release(existing_covers, meta.release_url):
        logger.debug(
            f"[green]Found existing cover images for this release URL: {meta.release_url}[/green]"
        )
        return True
    logger.debug(
        f"[yellow]Existing covers.json found but none match current release URL: {meta.release_url}[/yellow]"
    )
    _delete_covers_json(path, corrupted=False)
    return False


async def _download_cover_image(
    client: httpx.AsyncClient, img_type: str, url: str, temp_dir: Path
) -> str | None:
    file_ext = Path(url).suffix
    local_path = temp_dir / f"cover_{img_type}{file_ext}"
    try:
        logger.info(f"[dim]Downloading {img_type} cover from {url}[/dim]")
        response = await client.get(url)
    except Exception as error:
        logger.error(
            f"[red]Error downloading {img_type} cover: {error!s}[/red]"
        )
        return None
    if response.status_code != 200:
        logger.error(
            f"[red]Failed to download {img_type} cover: HTTP {response.status_code}[/red]"
        )
        return None
    await asyncio.to_thread(local_path.write_bytes, response.content)
    logger.info(
        f"[green]✓[/green] Downloaded {img_type} cover to {local_path}"
    )
    return str(local_path)


async def _download_cover_map(meta: Meta, temp_dir: Path) -> dict[str, str]:
    downloaded: dict[str, str] = {}
    cover_images = cast(Mapping[str, str], meta.bluray_cover_urls)
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True
    ) as client:
        for img_type, url in cover_images.items():
            local_path = await _download_cover_image(
                client, img_type, url, temp_dir
            )
            if local_path is not None:
                downloaded[img_type] = local_path
    return downloaded


async def download_cover_images(meta: Meta) -> bool:
    if "bluray_cover_urls" not in meta or not meta.bluray_cover_urls:
        logger.info("[yellow]No cover images to download[/yellow]")
        return False
    temp_dir = Path(meta.base_dir) / "tmp" / meta.uuid
    temp_dir.mkdir(parents=True, exist_ok=True)
    if await _reuse_existing_covers(meta, _covers_json_path(meta)):
        return True
    logger.info("[blue]Downloading cover images...[/blue]")
    downloaded_images = await _download_cover_map(meta, temp_dir)
    if not downloaded_images:
        logger.info("[yellow]No cover images were downloaded[/yellow]")
        return False
    meta.downloaded_bluray_cover_paths = downloaded_images
    logger.info(
        f"[green]Successfully downloaded {len(downloaded_images)} cover images[/green]"
    )
    return True


def _cover_key(img_id: str, *, script: bool) -> str:
    lowered = img_id.lower()
    if "front" in lowered:
        return "front"
    if "back" in lowered:
        return "back"
    slip_marker = "slipimage" if script else "slip"
    return "slip" if slip_marker in lowered else img_id


def _attribute_text(value: Any) -> str:
    if isinstance(value, AttributeValueList):
        return str(value[0]) if value else ""
    return str(value or "")


def _record_cover(
    covers: dict[str, str], img_id: str, url: str, *, script: bool
) -> None:
    if not url:
        return
    final_url = cast(str, clean_image_url(url)) if script else url
    covers[_cover_key(img_id, script=script)] = final_url


def _script_text(script: Any) -> str:
    value = script.string if script.string is not None else script.get_text()
    return str(value or "")


def _script_image_fragments(text: str) -> list[str]:
    if "append" not in text or "<img" not in text:
        return []
    pattern = re.compile(
        r"append\(\s*([\'\"])(?P<html><img\b.*?>)\1\s*\)", re.S | re.I
    )
    return [match.group("html") for match in pattern.finditer(text)]


def _cover_from_fragment(fragment_html: str) -> tuple[str, str] | None:
    fragment = BeautifulSoup(fragment_html, "lxml")
    img: Any = fragment.find("img")
    if img is None:
        return None
    return str(img.get("id") or "").strip(), str(img.get("src") or "").strip()


def _script_cover_images(soup: Any) -> dict[str, str]:
    covers: dict[str, str] = {}
    for script in soup.find_all("script"):
        for fragment_html in _script_image_fragments(_script_text(script)):
            parsed = _cover_from_fragment(fragment_html)
            if parsed is not None:
                _record_cover(covers, *parsed, script=True)
    return covers


def _valid_overlay_image(img: Any) -> bool:
    if img is None:
        return False
    return "id" in img.attrs and "src" in img.attrs


def _overlay_cover(div: Any) -> tuple[str, str] | None:
    img: Any = div.find("img")
    if not _valid_overlay_image(img):
        return None
    img_id = _attribute_text(img.get("id"))
    url = _attribute_text(img.get("src"))
    return (img_id, url) if img_id and url else None


def _overlay_cover_images(soup: Any) -> dict[str, str]:
    covers: dict[str, str] = {}
    for div in soup.find_all("div", class_="simple_overlay"):
        parsed = _overlay_cover(div)
        if parsed is not None:
            _record_cover(covers, *parsed, script=False)
    return covers


def extract_cover_images(html_content: str) -> dict[str, str]:
    soup: Any = BeautifulSoup(html_content, "lxml")
    covers = _script_cover_images(soup)
    return covers if covers else _overlay_cover_images(soup)


def clean_image_url(url: str | None) -> str | None:
    if not url:
        return url

    extensions = [".jpg", ".png", ".gif", ".jpeg", ".webp"]
    end_pos = None

    for ext in extensions:
        pos = url.lower().find(ext)
        if pos > 0:
            end_pos = pos + len(ext)
            break

    if end_pos:
        return url[:end_pos]
    return url


def _release_page_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.blu-ray.com/movies/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }


def _release_page_cache_path(meta: Meta, release_id: Any) -> Path:
    return (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / f"debug_release_{release_id}.html"
    )


async def _cached_release_page(meta: Meta, release_id: Any) -> str | None:
    path = _release_page_cache_path(meta, release_id)
    if not path.exists():
        return None
    logger.debug(
        f"[green]Found existing debug file for release ID {release_id}[/green]"
    )
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except Exception as error:
        logger.info(f"[yellow]Error reading cached file: {error!s}[/yellow]")
        return None
    if text and "No index" not in text:
        return text
    logger.info(
        "[yellow]Cached file exists but appears to be invalid, will fetch fresh data[/yellow]"
    )
    return None


async def _release_page_attempt(
    release_url: str, headers: dict[str, str], attempt: int, total: int
) -> Any | None:
    logger.debug(
        f"[yellow]Sending request to {release_url} (attempt {attempt}/{total})...[/yellow]"
    )
    try:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True
        ) as client:
            return await client.get(release_url, headers=headers)
    except httpx.RequestError as error:
        logger.info(
            f"[red]HTTP request error when accessing {release_url} (attempt {attempt}/{total}): {error!s}[/red]"
        )
        return None


def _release_page_kind(response: Any) -> str:
    if response.status_code == 200 and "No index" not in response.text:
        return "success"
    return "blocked" if "No index" in response.text else "status"


def _log_release_page_failure(
    response: Any, release_url: str, attempt: int, total: int
) -> None:
    if "No index" in response.text:
        logger.info(
            f"[red]Blocked by blu-ray.com when accessing {release_url} (attempt {attempt}/{total})[/red]"
        )
        return
    logger.error(
        f"[red]Failed to get release details, status code: {response.status_code} (attempt {attempt}/{total})[/red]"
    )


async def _fetch_release_page_html(release_url: str) -> str | None:
    delay = random.uniform(2, 4)  # nosec B311 - rate limiting only  # noqa: S311
    logger.debug(f"[dim]Waiting {delay:.2f} seconds before request...[/dim]")
    await asyncio.sleep(delay)
    total = 3
    backoff = 3.0
    headers = _release_page_headers()
    for index in range(total):
        attempt = index + 1
        response = await _release_page_attempt(
            release_url, headers, attempt, total
        )
        if response is not None:
            kind = _release_page_kind(response)
            if kind == "success":
                return str(response.text)
            _log_release_page_failure(response, release_url, attempt, total)
        if attempt == total:
            logger.info(
                "[red]Maximum retries reached, giving up on this release[/red]"
            )
            break
        backoff *= 2
        logger.info(f"[yellow]Retrying in {backoff:.1f} seconds...[/yellow]")
        await asyncio.sleep(backoff)
    return None


async def fetch_release_details(release: Release, meta: Meta) -> Release:
    release_url = str(release["url"])
    release_id = release.get("release_id", "0000000")
    logger.debug(
        f"[yellow]Fetching details for: {release['title']} - {release_url}[/yellow]"
    )
    response_text = await _cached_release_page(meta, release_id)
    if response_text is not None:
        return await parse_release_details(response_text, release, meta)
    response_text = await _fetch_release_page_html(release_url)
    if response_text is None:
        logger.error(
            "[red]Failed to retrieve release details after all attempts[/red]"
        )
        return release
    await _save_bluray_debug(
        _release_page_cache_path(meta, release_id),
        response_text,
        f"[dim]Saved release page to debug_release_{release_id}.html[/dim]",
    )
    return await parse_release_details(response_text, release, meta)


def _is_specs_subheading(element: Any) -> bool:
    if getattr(element, "name", None) != "span":
        return False
    classes = element.get("class", [])
    return "subheading" in cast(list[str], classes)


def _section_element_text(element: Any) -> str:
    if isinstance(element, str):
        return element
    if getattr(element, "name", None):
        return str(element.get_text())
    return ""


def extract_section(specs_td: Any, section_title: str) -> str | None:
    section_span: Any = specs_td.find(
        "span", class_="subheading", string=section_title
    )
    if not section_span:
        return None
    content: list[str] = []
    current: Any = section_span.next_sibling
    while current:
        if _is_specs_subheading(current):
            break
        text = _section_element_text(current)
        if text:
            content.append(text)
        current = getattr(current, "next_sibling", None)
    return "".join(content)


@dataclass(frozen=True)
class _LocalBlurayContext:
    video: dict[str, Any]
    audio: list[dict[str, Any]]
    subtitles: list[str]


@dataclass(frozen=True)
class _NormalizedAudioTrack:
    language: str
    format: str
    channels: str
    sample_rate: str
    bit_depth: str
    bitrate: str
    reduced_penalty: bool


@dataclass
class _AudioMatchCounts:
    full: int = 0
    partial: int = 0
    missing: int = 0
    reduced_missing: int = 0


@dataclass(frozen=True)
class _ScoredRelease:
    score: float
    release: Release
    logs: list[str]


def _score_log(message: str, logs: list[str]) -> None:
    logger.debug(message)
    logs.append(message)


def _first_bdinfo(meta: Meta) -> dict[str, Any]:
    if not meta.discs:
        return {}
    first = meta.discs[0]
    if not isinstance(first, Mapping):
        return {}
    first_map = cast(Mapping[str, Any], first)
    value = first_map.get("bdinfo", {})
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _local_video_specs(bdinfo: Mapping[str, Any]) -> dict[str, Any]:
    value = bdinfo.get("video", [])
    if not isinstance(value, list) or not value:
        return {}
    first = cast(list[Any], value)[0]
    return cast(dict[str, Any], first) if isinstance(first, dict) else {}


def _local_audio_specs(bdinfo: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = bdinfo.get("audio", [])
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], value)
        if isinstance(item, dict)
    ]


def _log_local_bluray_specs(
    video: Mapping[str, Any], audio: list[dict[str, Any]]
) -> None:
    if video:
        logger.debug(
            f"[dim]Local video: {video.get('codec', '')} {video.get('res', '')}"
        )
    for track in audio:
        logger.debug(
            f"[dim]Local audio: {track.get('language', '')} {track.get('codec', '')} {track.get('channels', '')} {track.get('bitrate', '')}"
        )


def _bd_summary_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid / "BD_SUMMARY_00.txt"


def _normalized_subtitle_line(line: str) -> str:
    value = line.strip()
    if value.startswith("* Subtitle:"):
        return value[2:].strip()
    return value if value.startswith("Subtitle:") else ""


def _subtitle_from_summary_line(line: str) -> tuple[str, float] | None:
    value = _normalized_subtitle_line(line)
    if not value:
        return None
    match = re.match(r"Subtitle:\s+(\w+)\s+/\s+([\d.]+)\s+kbps", value)
    if not match:
        return None
    return match.group(1), float(match.group(2))


def _summary_subtitles(text: str) -> list[str]:
    subtitles: list[str] = []
    for line in text.splitlines():
        parsed = _subtitle_from_summary_line(line)
        if parsed is None:
            continue
        language, bitrate = parsed
        if bitrate >= 1.0:
            subtitles.append(language)
            logger.debug(
                f"[green]✓ Keeping subtitle: {language} ({bitrate} kbps)[/green]"
            )
        else:
            logger.debug(
                f"[red]✗ Discarding subtitle due to size: {language} ({bitrate} kbps)[/red]"
            )
    return subtitles


async def _local_subtitles(meta: Meta) -> list[str]:
    path = _bd_summary_path(meta)
    if not path.exists():
        logger.info(f"[red]BD_SUMMARY file not found: {path}[/red]")
        return []
    logger.debug(f"[blue]Opening BD_SUMMARY file: {path}[/blue]")
    logger.debug(
        "[dim]Stripping extremely small subtitle tracks from bdinfo[/dim]"
    )
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except Exception as error:
        logger.error(f"[red]Error reading BD_SUMMARY file: {error!s}[/red]")
        return []
    subtitles = _summary_subtitles(text)
    if subtitles:
        logger.debug(
            f"[blue]Added subtitle languages: {', '.join(subtitles)}[/blue]"
        )
    else:
        logger.info("[yellow]No valid subtitles found to add.[/yellow]")
    return subtitles


async def _local_bluray_context(meta: Meta) -> _LocalBlurayContext:
    logger.debug("")
    logger.debug("Processing Local Details")
    logger.debug("----------------------------")
    logger.debug(f"[dim]Local disc count from meta: {len(meta.discs)}")
    bdinfo = _first_bdinfo(meta)
    video = _local_video_specs(bdinfo)
    audio = _local_audio_specs(bdinfo)
    _log_local_bluray_specs(video, audio)
    subtitles = await _local_subtitles(meta) if bdinfo else []
    return _LocalBlurayContext(video=video, audio=audio, subtitles=subtitles)


async def _detailed_bluray_releases(
    releases: Sequence[Release], meta: Meta
) -> list[Release]:
    detailed: list[Release] = []
    for index, release in enumerate(releases, 1):
        logger.info(
            f"[cyan]Processing release {index}/{len(releases)}: {release['title']} ({release['country']})"
        )
        detailed.append(await fetch_release_details(release, meta))
    logger.debug("")
    cli_ui.info_section("Processing Complete")
    cli_ui.info(f"Successfully processed {len(detailed)} releases")
    return detailed


def _missing_specs_penalty(
    specs: Mapping[str, Any], local_subtitles: list[str], logs: list[str]
) -> tuple[float, bool]:
    checks = (
        (not specs.get("video", {}), "video"),
        (not specs.get("audio", []), "audio"),
        (bool(local_subtitles) and not specs.get("subtitles", []), "subtitle"),
        (not specs.get("discs", {}), "disc"),
    )
    missing = [label for failed, label in checks if failed]
    for label in missing:
        _score_log(f"[red]✗[/red] Missing {label} info", logs)
        _score_log(f"[dim]Penalty for missing {label} info: 5.0[/dim]", logs)
    return len(missing) * 5.0, bool(missing)


def _expected_disc_format(size_gb: float) -> str:
    if size_gb < 25:
        return "bd-25"
    if size_gb < 50:
        return "bd-50"
    if size_gb < 66:
        return "bd-66"
    return "bd-100"


def _release_disc_format(specs: Mapping[str, Any]) -> str:
    discs = specs.get("discs", {})
    if not isinstance(discs, Mapping):
        return ""
    discs_map = cast(Mapping[str, Any], discs)
    return str(discs_map.get("format", ""))


def _local_disc_size(meta: Meta) -> float:
    bdinfo = _first_bdinfo(meta)
    try:
        return float(bdinfo.get("size", 0) or 0)
    except TypeError, ValueError:
        return 0.0


def _disc_format_kind(release_format: str, expected: str) -> str:
    lowered = release_format.lower()
    if expected in lowered:
        return "match"
    if "bd" in lowered and not any(char.isdigit() for char in lowered):
        return "generic"
    return "mismatch"


def _disc_penalty_result(
    kind: str,
    release_format: str,
    expected: str,
    size_gb: float,
    meta: Meta,
    logs: list[str],
) -> tuple[float, bool]:
    if kind == "match":
        _score_log(
            f"[green]✓[/green] Disc format match: {release_format} matches size {size_gb:.2f} GB",
            logs,
        )
        return 0.0, False
    if kind == "generic":
        _score_log(
            f"[yellow]⚠[/yellow] Generic BD format found: {release_format} for size {size_gb:.2f} GB",
            logs,
        )
        if meta.debug:
            _score_log(
                "[dim]Reduced penalty for generic BD format: 5.0[/dim]", logs
            )
        return 5.0, True
    _score_log(
        f"[yellow]⚠[/yellow] Disc format mismatch: {release_format} vs expected {expected.upper()} (size: {size_gb:.2f} GB)",
        logs,
    )
    if meta.debug:
        _score_log("[dim]Penalty for disc format mismatch: 50.0[/dim]", logs)
    return 50.0, False


def _disc_format_penalty(
    specs: Mapping[str, Any], meta: Meta, logs: list[str]
) -> tuple[float, bool]:
    release_format = _release_disc_format(specs)
    if not release_format or not _first_bdinfo(meta):
        return 0.0, False
    size_gb = _local_disc_size(meta)
    expected = _expected_disc_format(size_gb)
    return _disc_penalty_result(
        _disc_format_kind(release_format, expected),
        release_format,
        expected,
        size_gb,
        meta,
        logs,
    )


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _video_codec_label(release_codec: str, meta_codec: str) -> str:
    rules = (
        (("avc",), ("avc",), "AVC/H.264"),
        (("h.264",), ("avc", "h.264"), "AVC/H.264"),
        (("hevc",), ("hevc",), "HEVC/H.265"),
        (("h.265",), ("hevc", "h.265"), "HEVC/H.265"),
        (("vc-1",), ("vc-1",), "VC-1"),
        (("vc1",), ("vc1",), "VC-1"),
        (("mpeg-2",), ("mpeg-2",), "MPEG-2"),
        (("mpeg2",), ("mpeg2",), "MPEG-2"),
    )
    for release_markers, meta_markers, label in rules:
        if _contains_any(release_codec, release_markers) and _contains_any(
            meta_codec, meta_markers
        ):
            return label
    return ""


def _is_1080_resolution(value: str) -> bool:
    return "1080" in value


def _is_4k_resolution(value: str) -> bool:
    return "2160" in value or "4k" in value


def _video_resolution_label(release_res: str, meta_res: str) -> str:
    if _is_1080_resolution(release_res) and _is_1080_resolution(meta_res):
        return "1080p"
    if _is_4k_resolution(release_res) and _is_4k_resolution(meta_res):
        return "4K/2160p"
    return ""


def _release_video_mapping(specs: Mapping[str, Any]) -> Mapping[str, Any]:
    value = specs.get("video", {})
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _video_codec_penalty(
    release_codec: str, meta_codec: str, meta: Meta, logs: list[str]
) -> float:
    label = _video_codec_label(release_codec, meta_codec)
    if label:
        _score_log(f"[green]✓[/green] Video codec match: {label}", logs)
        return 0.0
    _score_log(
        f"[red]✗[/red] Video codec mismatch: {release_codec} vs {meta_codec}",
        logs,
    )
    if meta.debug:
        _score_log("[dim]Penalty for video codec mismatch 80.0[/dim]", logs)
    return 80.0


def _video_resolution_penalty(
    release_res: str, meta_res: str, meta: Meta, logs: list[str]
) -> float:
    label = _video_resolution_label(release_res, meta_res)
    if label:
        _score_log(f"[green]✓[/green] Resolution match: {label}", logs)
        return 0.0
    _score_log(
        f"[red]✗[/red] Resolution mismatch: {release_res} vs {meta_res}",
        logs,
    )
    if meta.debug:
        _score_log("[dim]Penalty for resolution mismatch 80.0[/dim]", logs)
    return 80.0


def _video_penalty(
    specs: Mapping[str, Any],
    local_video: Mapping[str, Any],
    meta: Meta,
    logs: list[str],
) -> float:
    if "video" not in specs or not local_video:
        _score_log("[yellow]?[/yellow] Cannot compare video formats", logs)
        return 5.0
    release_video = _release_video_mapping(specs)
    release_codec = str(release_video.get("codec", "")).lower()
    meta_codec = str(local_video.get("codec", "")).lower()
    release_res = str(release_video.get("resolution", "")).lower()
    meta_res = str(local_video.get("res", "")).lower()
    return _video_codec_penalty(
        release_codec, meta_codec, meta, logs
    ) + _video_resolution_penalty(release_res, meta_res, meta, logs)


def _audio_text(track: Mapping[str, Any], key: str) -> str:
    return str(track.get(key, "")).lower().replace("audio", "")


def _is_atmos_track(track: Mapping[str, Any], channels: str) -> bool:
    marker = str(track.get("atmos_why_you_be_like_this", "")).lower()
    return marker == "atmos" or "atmos" in channels


def _normalized_atmos_format(value: str, atmos: bool) -> str:
    if not atmos:
        return value
    if "truehd" in value:
        return "dolby truehd atmos"
    if "dolby" in value:
        return "dolby atmos"
    return value


def _normalized_atmos_channels(
    value: str, sample_rate: str, atmos: bool
) -> str:
    if not atmos or value.strip() not in {"atmos audio", "atmos", ""}:
        return value
    return (
        sample_rate if sample_rate in {"7.1", "5.1", "2.0", "1.0"} else "7.1"
    )


def _normalized_sample_bitrate(
    sample_rate: str, bitrate: str, atmos: bool
) -> tuple[str, str]:
    if atmos and "khz" in bitrate and "khz" not in sample_rate:
        return bitrate, ""
    return sample_rate, bitrate


def _bit_depth_parts(bit_depth: str) -> tuple[str, str]:
    bitrate_match = re.search(r"(\d+\s*kbps)", bit_depth)
    depth_match = re.search(r"(\d+)-bit", bit_depth)
    bitrate = bitrate_match.group(1) if bitrate_match else ""
    depth = depth_match.group(1) + "-bit" if depth_match else ""
    return depth, bitrate


def _normalized_depth_bitrate(
    bit_depth: str, bitrate: str, atmos: bool
) -> tuple[str, str]:
    if atmos and "kbps" in bit_depth:
        depth, embedded_bitrate = _bit_depth_parts(bit_depth)
        return depth, embedded_bitrate or bitrate
    return ("" if "dn -" in bit_depth else bit_depth), bitrate


def _reduced_audio_penalty(index: int, bitrate: str) -> bool:
    if index <= 0 or not bitrate or "kbps" not in bitrate:
        return False
    return int(bitrate.replace("kbps", "").strip()) <= 258


def _normalized_audio_track(
    track: Mapping[str, Any], index: int
) -> _NormalizedAudioTrack:
    language = _audio_text(track, "language")
    audio_format = _audio_text(track, "codec")
    channels = _audio_text(track, "channels")
    sample_rate = str(track.get("sample_rate", "")).lower()
    bit_depth = str(track.get("bit_depth", "")).lower()
    bitrate = str(track.get("bitrate", "")).lower()
    atmos = _is_atmos_track(track, channels)
    audio_format = _normalized_atmos_format(audio_format, atmos)
    channels = _normalized_atmos_channels(channels, sample_rate, atmos)
    sample_rate, bitrate = _normalized_sample_bitrate(
        sample_rate, bitrate, atmos
    )
    bit_depth, bitrate = _normalized_depth_bitrate(bit_depth, bitrate, atmos)
    return _NormalizedAudioTrack(
        language=language,
        format=audio_format,
        channels=channels,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        bitrate=bitrate,
        reduced_penalty=_reduced_audio_penalty(index, bitrate),
    )


def _audio_format_matches(local_format: str, release_track: str) -> bool:
    rules = (
        ("lpcm", ("pcm", "lpcm")),
        ("dts-hd", ("dts-hd",)),
        ("dts", ("dts",)),
        ("dolby", ("dolby",)),
        ("truehd", ("truehd",)),
        ("atmos", ("atmos",)),
    )
    for local_marker, release_markers in rules:
        if local_marker in local_format and _contains_any(
            release_track, release_markers
        ):
            return True
    return False


def _audio_channel_matches(local_channels: str, release_track: str) -> bool:
    rules: dict[str, tuple[str, ...]] = {
        "5.1": ("5.1",),
        "7.1": ("7.1",),
        "2.0": ("2.0", "stereo"),
        "1.0": ("1.0", "mono"),
    }
    for local_marker, release_markers in rules.items():
        if local_marker in local_channels:
            return _contains_any(release_track, release_markers)
    return False


def _normalized_value_matches(
    value: str, release_track: str, *, strip: str = " "
) -> bool:
    if not value:
        return False
    normalized_value = value.replace(strip, "")
    normalized_release = release_track.replace(" ", "")
    return value in release_track or normalized_value in normalized_release


def _audio_candidate_score(
    local: _NormalizedAudioTrack, release_track: str
) -> tuple[int, int, bool]:
    release_lower = release_track.lower()
    if not local.language or local.language not in release_lower:
        return 0, 0, False
    format_match = _audio_format_matches(local.format, release_lower)
    channel_match = _audio_channel_matches(local.channels, release_lower)
    core_score = 1 + int(format_match) + int(channel_match)
    supplemental = sum(
        (
            _normalized_value_matches(local.sample_rate, release_lower),
            _normalized_value_matches(
                local.bit_depth, release_lower, strip="-"
            ),
            _normalized_value_matches(local.bitrate, release_lower),
        )
    )
    return core_score + supplemental, core_score, format_match or channel_match


def _best_audio_candidate(
    local: _NormalizedAudioTrack, release_tracks: list[str]
) -> tuple[int, int, bool]:
    best_score = 0
    best_core = 0
    best_index = -1
    found = False
    for index, release_track in enumerate(release_tracks):
        score, core, candidate_found = _audio_candidate_score(
            local, release_track
        )
        found = found or candidate_found
        if score > best_score:
            best_score = score
            best_core = core
            best_index = index
    return best_index, best_core, found


def _log_audio_match(
    local: _NormalizedAudioTrack,
    matched_track: str,
    core: int,
    logs: list[str],
) -> bool:
    quality = core / 3.0
    if quality >= 1:
        _score_log(
            f"[green]✓[/green] Found good match for {local.language} {local.format} {local.channels} track: '{matched_track}' (match quality: 100%)",
            logs,
        )
        return True
    _score_log(
        f"[yellow]⚠[/yellow] Found partial match for {local.language} {local.format} {local.channels} track: '{matched_track}' (match quality: {int(quality * 100)}%)",
        logs,
    )
    return False


def _record_missing_audio(
    local: _NormalizedAudioTrack, counts: _AudioMatchCounts, logs: list[str]
) -> None:
    counts.missing += 1
    if local.reduced_penalty:
        counts.reduced_missing += 1
        _score_log(
            f"[red]✗[/red] No match found for {local.language} {local.format} {local.channels} track (Low bitrate, half penalty)",
            logs,
        )
        return
    _score_log(
        f"[red]✗[/red] No match found for {local.language} {local.format} {local.channels} {local.bitrate} track",
        logs,
    )


def _consume_audio_track(
    local: _NormalizedAudioTrack,
    release_tracks: list[str],
    counts: _AudioMatchCounts,
    logs: list[str],
) -> None:
    index, core, found = _best_audio_candidate(local, release_tracks)
    if not found or index < 0:
        _record_missing_audio(local, counts, logs)
        return
    matched = release_tracks.pop(index)
    if _log_audio_match(local, matched, core, logs):
        counts.full += 1
    else:
        counts.partial += 1


def _audio_match_penalty(total: int, counts: _AudioMatchCounts) -> float:
    if counts.full == total:
        return 0.0
    if total == 1:
        return 5.0 if counts.partial == 1 else 10.0
    normal_missing = counts.missing - counts.reduced_missing
    return (
        counts.partial * 2.5
        + normal_missing * 5.0
        + counts.reduced_missing * 2.5
    )


def _log_audio_summary(
    total: int, counts: _AudioMatchCounts, logs: list[str]
) -> None:
    full_pct = (counts.full / total) * 100
    partial_pct = (counts.partial / total) * 100
    if counts.full:
        _score_log(
            f"[green]✓[/green] Audio tracks with good matches: {counts.full}/{total} ({full_pct:.1f}% of tracks)",
            logs,
        )
        if counts.partial:
            _score_log(
                f"[yellow]⚠[/yellow] Audio tracks with partial matches: {counts.partial}/{total} ({partial_pct:.1f}% of tracks)",
                logs,
            )
        return
    if counts.partial:
        _score_log(
            f"[yellow]⚠[/yellow] There were only partial audio track matches: {counts.partial}/{total}",
            logs,
        )
        return
    _score_log("[red]✗[/red] No audio tracks match!", logs)


def _extra_audio_penalty(
    release_tracks: list[str], meta: Meta, logs: list[str]
) -> float:
    for track in release_tracks:
        _score_log(
            f"[yellow]⚠[/yellow] Release has extra audio track not in BDInfo: {track}",
            logs,
        )
    if not release_tracks:
        return 0.0
    penalty = len(release_tracks) * 5.0
    _score_log(
        f"[red]-[/red] Found {len(release_tracks)} additional audio tracks in release not in BDInfo",
        logs,
    )
    if meta.debug:
        _score_log(
            f"[dim]Extra audio tracks penalty: {penalty:.1f} points[/dim]",
            logs,
        )
    return penalty


def _audio_penalty(
    specs: Mapping[str, Any],
    local_audio: list[dict[str, Any]],
    meta: Meta,
    logs: list[str],
) -> float:
    if "audio" not in specs or not local_audio:
        _score_log("[yellow]?[/yellow] Cannot compare audio tracks", logs)
        return 5.0
    release_tracks = list(cast(list[str], specs.get("audio", [])))
    counts = _AudioMatchCounts()
    for index, raw_track in enumerate(local_audio):
        _consume_audio_track(
            _normalized_audio_track(raw_track, index),
            release_tracks,
            counts,
            logs,
        )
    total = len(local_audio)
    penalty = _audio_match_penalty(total, counts)
    if meta.debug:
        _score_log(f"[dim]Audio penalty: {penalty:.1f}[/dim]", logs)
    _log_audio_summary(total, counts, logs)
    return penalty + _extra_audio_penalty(release_tracks, meta, logs)


def _subtitle_match_index(
    meta_subtitle: str, release_subtitles: list[str]
) -> int:
    local = meta_subtitle.lower()
    for index, release_subtitle in enumerate(release_subtitles):
        candidate = release_subtitle.lower()
        if local in candidate or candidate in local:
            return index
    return -1


def _consume_subtitle(
    meta_subtitle: str, release_subtitles: list[str], logs: list[str]
) -> bool:
    index = _subtitle_match_index(meta_subtitle, release_subtitles)
    if index < 0:
        _score_log(
            f"[red]✗[/red] No match found for subtitle: {meta_subtitle}", logs
        )
        return False
    matched = release_subtitles.pop(index)
    _score_log(
        f"[green]✓[/green] Subtitle match found: {meta_subtitle} -> {matched}",
        logs,
    )
    return True


def _subtitle_base_penalty(total: int, matches: int) -> float:
    if total == 1 and matches == 0:
        return 10.0
    return 5.0 * (total - matches)


def _extra_subtitle_penalty(
    release_subtitles: list[str], meta: Meta, logs: list[str]
) -> float:
    for subtitle in release_subtitles:
        _score_log(
            f"[yellow]⚠[/yellow] Release has extra subtitle not in BDInfo: {subtitle}",
            logs,
        )
    if not release_subtitles:
        return 0.0
    penalty = len(release_subtitles) * 5.0
    _score_log(
        f"[red]-[/red] Found {len(release_subtitles)} additional subtitles in release not in BDInfo",
        logs,
    )
    if meta.debug:
        _score_log(
            f"[dim]Extra subtitles penalty: {penalty:.1f} points[/dim]", logs
        )
    return penalty


def _log_subtitle_match_summary(
    total: int, matches: int, penalty: float, meta: Meta, logs: list[str]
) -> None:
    if meta.debug:
        _score_log(f"[dim]Subtitle penalty: {penalty:.1f}[/dim]", logs)
    if matches:
        _score_log(
            f"[green]✓[/green] Subtitle matches: {matches}/{total} ({(matches / total) * 100:.1f}%)",
            logs,
        )
        return
    _score_log("[red]✗[/red] No subtitle tracks match!", logs)


def _subtitle_penalty(
    specs: Mapping[str, Any],
    local_subtitles: list[str],
    meta: Meta,
    logs: list[str],
) -> float:
    if "subtitles" not in specs or not local_subtitles:
        _score_log("[yellow]?[/yellow] Cannot compare subtitles", logs)
        return 5.0
    release_subtitles = list(cast(list[str], specs.get("subtitles", [])))
    matches = sum(
        _consume_subtitle(subtitle, release_subtitles, logs)
        for subtitle in local_subtitles
    )
    total = len(local_subtitles)
    penalty = _subtitle_base_penalty(total, matches)
    _log_subtitle_match_summary(total, matches, penalty, meta, logs)
    return penalty + _extra_subtitle_penalty(release_subtitles, meta, logs)


def _score_release(
    release: Release,
    context: _LocalBlurayContext,
    meta: Meta,
    index: int,
    total: int,
) -> _ScoredRelease:
    logs: list[str] = []
    logger.debug(
        f"\n[bold blue]=== Release {index}/{total}: {release['title']} ({release['country']}) ===[/bold blue]"
    )
    _score_log(f"[blue]Release URL: {release['url']}[/blue]", logs)
    if "specs" not in release:
        score = 20.0
        generic = False
        specs_missing = True
        _score_log(
            "[red]✗[/red] No specifications available for this release", logs
        )
    else:
        specs_value = release.get("specs", {})
        specs: Mapping[str, Any] = (
            cast(Mapping[str, Any], specs_value)
            if isinstance(specs_value, Mapping)
            else cast(Mapping[str, Any], {})
        )
        missing_penalty, specs_missing = _missing_specs_penalty(
            specs, context.subtitles, logs
        )
        disc_penalty, generic = _disc_format_penalty(specs, meta, logs)
        score = 100.0 - missing_penalty - disc_penalty
        score -= _video_penalty(specs, context.video, meta, logs)
        score -= _audio_penalty(specs, context.audio, meta, logs)
        score -= _subtitle_penalty(specs, context.subtitles, meta, logs)
    _score_log(
        f"[blue]Final score: {score:.1f}/100 for {release['title']} ({release['country']})[/blue]",
        logs,
    )
    _score_log("", logs)
    release["_generic_format"] = generic
    release["_specs_missing"] = specs_missing
    return _ScoredRelease(score=score, release=release, logs=logs)


def _score_releases(
    releases: list[Release], context: _LocalBlurayContext, meta: Meta
) -> list[_ScoredRelease]:
    scored = [
        _score_release(release, context, meta, index, len(releases))
        for index, release in enumerate(releases, 1)
    ]
    return sorted(scored, key=lambda item: item.score, reverse=True)


async def _apply_matched_release(meta: Meta, release: Release) -> None:
    region_code = map_country_to_region_code(str(release["country"]))
    meta.region = region_code or ""
    meta.distributor = str(release["publisher"]).upper()
    meta.release_url = str(release["url"])
    if "cover_images" in release:
        meta.bluray_cover_urls = release["cover_images"]
        await download_cover_images(meta)
    logger.info(
        f"[yellow]Set region code to: {region_code}, distributor to: {str(release['publisher']).upper()}[/yellow]"
    )


def _interactive_bluray_matching(meta: Meta) -> bool:
    return not meta.unattended or bool(meta.unattended_confirm)


def _no_suitable_release(best: _ScoredRelease) -> list[Release]:
    cli_ui.warning(
        f"No suitable release found. Best match was {best.release['title']} ({best.release['country']}) with score {best.score:.1f}/100"
    )
    return []


async def _confirm_single_scored_release(
    best: _ScoredRelease, detailed: list[Release], meta: Meta
) -> list[Release]:
    cli_ui.info(
        f"Single match found: {best.release['title']} ({best.release['country']}) with score {best.score:.1f}/100"
    )
    try:
        accepted = cli_ui.ask_yes_no(
            "Do you want to use this release?", default=False
        )
    except KeyboardInterrupt:
        logger.info("[red]Operation cancelled.[/red]")
        return detailed
    if not accepted:
        cli_ui.warning("No release selected.")
        return []
    await _apply_matched_release(meta, best.release)
    return detailed


async def _single_scored_release(
    best: _ScoredRelease, detailed: list[Release], meta: Meta
) -> list[Release]:
    if best.score == 100:
        cli_ui.info(
            f"Single perfect match found: {best.release['title']} ({best.release['country']}) with score {best.score:.1f}/100"
        )
        await _apply_matched_release(meta, best.release)
        return detailed
    if _interactive_bluray_matching(meta):
        return await _confirm_single_scored_release(best, detailed, meta)
    if best.score > float(meta.bluray_single_score or 100):
        cli_ui.info(
            f"Best match: {best.release['title']} ({best.release['country']}) with score {best.score:.1f}/100"
        )
        await _apply_matched_release(meta, best.release)
        return detailed
    return _no_suitable_release(best)


def _close_scored_releases(
    scored: list[_ScoredRelease],
) -> list[_ScoredRelease]:
    best_score = scored[0].score
    return [item for item in scored if best_score - item.score <= 40]


def _log_close_match_flags(close: list[_ScoredRelease]) -> None:
    if any(bool(item.release.get("_generic_format", False)) for item in close):
        logger.info(
            "[red]Note: Generic BD format found, please confirm the release.[/red]"
        )
    if any(bool(item.release.get("_specs_missing", False)) for item in close):
        logger.info(
            "[red]Note: Missing specs in release, please confirm the release.[/red]"
        )


def _log_close_matches(close: list[_ScoredRelease]) -> None:
    logger.info(
        "[yellow]Multiple releases are within 40 points of the best match. Please confirm which release to use:[/yellow]"
    )
    _log_close_match_flags(close)
    for index, item in enumerate(close, 1):
        logger.info(
            f"{index}. [blue]{item.release['title']} ({item.release['country']})[/blue] - Score: {item.score:.1f}/100"
        )


def _print_scored_release_logs(item: _ScoredRelease) -> None:
    logger.info(
        f"[yellow]Logs for release: {item.release['title']} ({item.release['country']})[/yellow]"
    )
    for message in item.logs:
        logger.info(message)


def _prompt_log_index(close: list[_ScoredRelease]) -> bool:
    try:
        raw = cli_ui.ask_string(
            f"Enter the release number (1-{len(close)}) to print logs: "
        )
        index = int((raw or "").strip())
    except ValueError:
        logger.info("[red]Invalid input. Please enter a valid number.[/red]")
        return False
    except KeyboardInterrupt:
        logger.info("[red]Operation cancelled.[/red]")
        return True
    if not 1 <= index <= len(close):
        logger.info(
            f"[red]Invalid selection. Please enter a number between 1 and {len(close)}.[/red]"
        )
        return False
    _print_scored_release_logs(close[index - 1])
    return False


def _selected_close_index(value: str, size: int) -> int | None:
    try:
        index = int(value)
    except ValueError:
        logger.info("[red]Invalid input. Please enter a number or 'n'.[/red]")
        return None
    if 1 <= index <= size:
        return index - 1
    logger.info(
        f"[red]Invalid selection. Please enter a number between 1 and {size}.[/red]"
    )
    return None


def _close_selection_value(
    value: str, close: list[_ScoredRelease]
) -> tuple[str, int | None]:
    if value == "n":
        return "skip", None
    if value == "p":
        return "logs", None
    index = _selected_close_index(value, len(close))
    return ("select", index) if index is not None else ("retry", None)


def _read_close_selection(
    close: list[_ScoredRelease],
) -> tuple[str, int | None] | None:
    try:
        raw = cli_ui.ask_string("Selection: ")
        return _close_selection_value((raw or "").strip().lower(), close)
    except KeyboardInterrupt:
        logger.info("[red]Operation cancelled.[/red]")
        return None


def _close_auxiliary_result(
    action: str, close: list[_ScoredRelease], detailed: list[Release]
) -> tuple[bool, list[Release] | None]:
    if action == "skip":
        cli_ui.warning("No release selected.")
        return True, []
    if action == "logs":
        return (_prompt_log_index(close), detailed)
    return False, None


async def _apply_selected_close_release(
    close: list[_ScoredRelease],
    index: int,
    detailed: list[Release],
    meta: Meta,
) -> list[Release]:
    selected = close[index].release
    cli_ui.info(f"Selected: {selected['title']} ({selected['country']})")
    await _apply_matched_release(meta, selected)
    return detailed


def _normalized_close_auxiliary_result(
    terminal: bool, result: list[Release] | None
) -> tuple[bool, list[Release]]:
    if not terminal:
        return False, []
    return True, result if result is not None else []


async def _selected_close_action_result(
    action: str,
    index: int | None,
    close: list[_ScoredRelease],
    detailed: list[Release],
    meta: Meta,
) -> tuple[bool, list[Release]]:
    if action != "select" or index is None:
        return False, []
    selected = await _apply_selected_close_release(
        close, index, detailed, meta
    )
    return True, selected


async def _close_selection_iteration(
    close: list[_ScoredRelease], detailed: list[Release], meta: Meta
) -> tuple[bool, list[Release]]:
    parsed = _read_close_selection(close)
    if parsed is None:
        return True, detailed
    action, index = parsed
    normalized = _normalized_close_auxiliary_result(
        *_close_auxiliary_result(action, close, detailed)
    )
    if normalized[0]:
        return normalized
    return await _selected_close_action_result(
        action, index, close, detailed, meta
    )


async def _interactive_close_selection(
    close: list[_ScoredRelease], detailed: list[Release], meta: Meta
) -> list[Release]:
    _log_close_matches(close)
    while True:
        logger.info(
            "Enter the number of the release to use, 'p' to print logs for a release, or 'n' to skip:"
        )
        done, result = await _close_selection_iteration(close, detailed, meta)
        if done:
            return result


async def _multiple_close_selection(
    best: _ScoredRelease,
    close: list[_ScoredRelease],
    detailed: list[Release],
    meta: Meta,
) -> list[Release]:
    if _interactive_bluray_matching(meta):
        return await _interactive_close_selection(close, detailed, meta)
    if best.score > float(meta.bluray_score or 100):
        cli_ui.info(
            f"Best match: {best.release['title']} ({best.release['country']}) with score {best.score:.1f}/100"
        )
        await _apply_matched_release(meta, best.release)
        return detailed
    return _no_suitable_release(best)


async def _unattended_best_selection(
    best: _ScoredRelease, detailed: list[Release], meta: Meta
) -> list[Release]:
    if best.score <= float(meta.bluray_score or 100):
        return _no_suitable_release(best)
    cli_ui.info(
        f"Best match: {best.release['title']} ({best.release['country']}) with score {best.score:.1f}/100"
    )
    await _apply_matched_release(meta, best.release)
    return detailed


async def _confirm_isolated_best(
    best: _ScoredRelease, detailed: list[Release], meta: Meta
) -> list[Release]:
    logger.info(
        "[red]This is the probably the best match, but it is not a perfect match.[/red]"
    )
    logger.info(
        "[yellow]All other releases have a score at least 40 points lower."
    )
    _print_scored_release_logs(best)
    try:
        accepted = cli_ui.ask_yes_no(
            "Do you want to use this release?", default=False
        )
    except KeyboardInterrupt:
        logger.info("[red]Operation cancelled.[/red]")
        return detailed
    if not accepted:
        cli_ui.warning("No release selected.")
        return []
    await _apply_matched_release(meta, best.release)
    return detailed


async def _isolated_best_selection(
    best: _ScoredRelease, detailed: list[Release], meta: Meta
) -> list[Release]:
    if not _interactive_bluray_matching(meta):
        return await _unattended_best_selection(best, detailed, meta)
    return await _confirm_isolated_best(best, detailed, meta)


async def _select_scored_releases(
    scored: list[_ScoredRelease], detailed: list[Release], meta: Meta
) -> list[Release]:
    if not scored:
        return detailed
    best = scored[0]
    if len(scored) == 1:
        return await _single_scored_release(best, detailed, meta)
    close = _close_scored_releases(scored)
    if len(close) > 1:
        return await _multiple_close_selection(best, close, detailed, meta)
    return await _isolated_best_selection(best, detailed, meta)


async def process_all_releases(
    releases: Sequence[Release], meta: Meta
) -> list[Release]:
    if not releases:
        return []
    context = await _local_bluray_context(meta)
    detailed = await _detailed_bluray_releases(releases, meta)
    scored = _score_releases(detailed, context, meta)
    return await _select_scored_releases(scored, detailed, meta)


def map_country_to_region_code(country_name: str) -> str | None:
    country_map = {
        "Afghanistan": "AFG",
        "Albania": "ALB",
        "Algeria": "ALG",
        "Andorra": "AND",
        "Angola": "ANG",
        "Argentina": "ARG",
        "Armenia": "ARM",
        "Aruba": "ARU",
        "Australia": "AUS",
        "Austria": "AUT",
        "Azerbaijan": "AZE",
        "Bahamas": "BAH",
        "Bahrain": "BHR",
        "Bangladesh": "BAN",
        "Barbados": "BRB",
        "Belarus": "BLR",
        "Belgium": "BEL",
        "Belize": "BLZ",
        "Benin": "BEN",
        "Bermuda": "BER",
        "Bhutan": "BHU",
        "Bolivia": "BOL",
        "Bosnia and Herzegovina": "BIH",
        "Botswana": "BOT",
        "Brazil": "BRA",
        "British Virgin Islands": "VGB",
        "Brunei": "BRU",
        "Burkina Faso": "BFA",
        "Burundi": "BDI",
        "Cambodia": "CAM",
        "Cameroon": "CMR",
        "Canada": "CAN",
        "Cape Verde": "CPV",
        "Cayman Islands": "CAY",
        "Central African Republic": "CTA",
        "Chad": "CHA",
        "Chile": "CHI",
        "China": "CHN",
        "Colombia": "COL",
        "Comoros": "COM",
        "Congo": "CGO",
        "Cook Islands": "COK",
        "Costa Rica": "CRC",
        "Croatia": "CRO",
        "Cuba": "CUB",
        "Cyprus": "CYP",
        "Dominican Republic": "DOM",
        "Ecuador": "ECU",
        "Egypt": "EGY",
        "El Salvador": "SLV",
        "Equatorial Guinea": "EQG",
        "Eritrea": "ERI",
        "Ethiopia": "ETH",
        "Fiji": "FIJ",
        "France": "FRA",
        "Gabon": "GAB",
        "Gambia": "GAM",
        "Georgia": "GEO",
        "Germany": "GER",
        "Ghana": "GHA",
        "Greece": "GRE",
        "Grenada": "GRN",
        "Guatemala": "GUA",
        "Guinea": "GUI",
        "Guyana": "GUY",
        "Haiti": "HAI",
        "Honduras": "HON",
        "Hong Kong": "HKG",
        "Hungary": "HUN",
        "Iceland": "ISL",
        "India": "IND",
        "Indonesia": "IDN",
        "Iran": "IRN",
        "Iraq": "IRQ",
        "Ireland": "IRL",
        "Israel": "ISR",
        "Italy": "ITA",
        "Jamaica": "JAM",
        "Japan": "JPN",
        "Jordan": "JOR",
        "Kazakhstan": "KAZ",
        "Kenya": "KEN",
        "Kuwait": "KUW",
        "Kyrgyzstan": "KGZ",
        "Laos": "LAO",
        "Lebanon": "LBN",
        "Liberia": "LBR",
        "Libya": "LBY",
        "Liechtenstein": "LIE",
        "Luxembourg": "LUX",
        "Macau": "MAC",
        "Madagascar": "MAD",
        "Malaysia": "MAS",
        "Malta": "MLT",
        "Mexico": "MEX",
        "Monaco": "MON",
        "Mongolia": "MNG",
        "Morocco": "MAR",
        "Mozambique": "MOZ",
        "Namibia": "NAM",
        "Nepal": "NEP",
        "Netherlands": "NLD",
        "New Zealand": "NZL",
        "Nicaragua": "NCA",
        "Niger": "NIG",
        "North Korea": "PRK",
        "North Macedonia": "MKD",
        "Norway": "NOR",
        "Oman": "OMA",
        "Pakistan": "PAK",
        "Panama": "PAN",
        "Papua New Guinea": "PNG",
        "Paraguay": "PAR",
        "Peru": "PER",
        "Philippines": "PHI",
        "Poland": "POL",
        "Portugal": "POR",
        "Puerto Rico": "PUR",
        "Qatar": "QAT",
        "Romania": "ROU",
        "Russia": "RUS",
        "Rwanda": "RWA",
        "Saint Lucia": "LCA",
        "Samoa": "SAM",
        "San Marino": "SMR",
        "Saudi Arabia": "KSA",
        "Senegal": "SEN",
        "Serbia": "SRB",
        "Singapore": "SIN",
        "South Africa": "RSA",
        "South Korea": "KOR",
        "Spain": "ESP",
        "Sri Lanka": "LKA",
        "Sudan": "SDN",
        "Suriname": "SUR",
        "Switzerland": "SUI",
        "Syria": "SYR",
        "Chinese Taipei": "TWN",
        "Tajikistan": "TJK",
        "Tanzania": "TAN",
        "Thailand": "THA",
        "Trinidad and Tobago": "TRI",
        "Tunisia": "TUN",
        "Turkey": "TUR",
        "Uganda": "UGA",
        "Ukraine": "UKR",
        "United Arab Emirates": "UAE",
        "United Kingdom": "GBR",
        "United States": "USA",
        "Uruguay": "URU",
        "Uzbekistan": "UZB",
        "Venezuela": "VEN",
        "Vietnam": "VIE",
        "Zambia": "ZAM",
        "Zimbabwe": "ZIM",
    }

    region_code = country_map.get(country_name)
    if not region_code:
        region_code = None

    return region_code
