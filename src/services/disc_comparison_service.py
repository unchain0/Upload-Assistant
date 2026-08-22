import difflib
import re
from pathlib import Path
from typing import Any

from src.domain_models.release import Meta
from src.services.runtime_support import logger

PLAYLIST_VARIATION_PATTERN = re.compile(r"/\s*DN\s*-\d+dB", re.IGNORECASE)
BITRATE_VARIATION_PATTERN = re.compile(
    r"\d+([.,]\d+)?(?=\s*kbps)", re.IGNORECASE
)
BBCODE_PATTERN = re.compile(r"\[[^\]]*\]")
HTML_PATTERN = re.compile(r"<[^>]*>")


def get_relevant_lines(
    meta: Meta, duplicate_content: str
) -> tuple[list[str], list[str]]:
    """
    Extracts and normalizes relevant BDInfo lines for comparison between source and duplicate content.
    """
    summary, extended_summary = load_bdinfo_file(meta)
    clean_duplicate = remove_formatting(duplicate_content)

    clean_sum, clean_ext, clean_dup = remove_playlist_variations(
        summary, extended_summary, clean_duplicate
    )

    is_extended = any(
        key in clean_dup for key in ("PLAYLIST REPORT:", "DISC INFO:")
    )
    is_full = is_extended and "Video:" in clean_dup

    target_lines = normalize_and_filter(clean_dup, strict_mode=is_full)
    source_content = clean_ext if (is_extended and not is_full) else clean_sum
    source_lines = normalize_and_filter(source_content)

    return source_lines, target_lines


def _technical_line_relevant(line_lower: str) -> bool:
    markers = ("kbps", "presentation graphics", "subtitle:")
    return any(marker in line_lower for marker in markers)


def _strict_line_allowed(clean_line: str, strict_mode: bool) -> bool:
    if not strict_mode:
        return True
    keywords = ("Video:", "Audio:", "Subtitle:")
    return any(keyword in clean_line for keyword in keywords)


def _normalized_technical_line(line: str, strict_mode: bool) -> str | None:
    clean_line = line.strip()
    if not _technical_line_relevant(clean_line.lower()):
        return None
    if not _strict_line_allowed(clean_line, strict_mode):
        return None
    return " ".join(clean_line.split())


def normalize_and_filter(content: str, strict_mode: bool = False) -> list[str]:
    """
    Filters content to keep only relevant technical lines and normalizes whitespace.
    """
    results: list[str] = []
    for line in content.splitlines():
        normalized = _normalized_technical_line(line, strict_mode)
        if normalized is not None:
            results.append(normalized)
    return results


def remove_playlist_variations(
    summary: str, extended: str, duplicate: str
) -> tuple[str, str, str]:
    """
    Removes technical variations that differ between playlists but represent the same media content.
    """

    def process_content(text: str) -> str:
        if not text:
            return ""

        text = re.sub(PLAYLIST_VARIATION_PATTERN, "", text)
        cleaned_lines: list[str] = []

        for line in text.splitlines():
            line_lower = line.lower()

            if (
                "presentation graphics" in line_lower
                or "subtitle:" in line_lower
            ):
                line = re.sub(BITRATE_VARIATION_PATTERN, "", line).rstrip()
                if line.endswith("kbps"):
                    line = line[:-4].rstrip()
                if line.endswith("/"):
                    line = line[:-1].rstrip()

            if line.startswith("*"):
                line = line[:1].rstrip()

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    return (
        process_content(summary),
        process_content(extended),
        process_content(duplicate),
    )


def _comparison_results(
    source_lines: list[str], target_lines: list[str]
) -> tuple[list[dict[str, str]], dict[str, int]]:
    results: list[dict[str, str]] = []
    stats = {"+ ": 0, "- ": 0}
    for line in difflib.ndiff(source_lines, target_lines):
        if line.startswith("? "):
            continue
        prefix, content = line[:2], line[2:].strip()
        results.append({"prefix": prefix, "content": content})
        if prefix in stats:
            stats[prefix] += 1
    return results, stats


def _comparison_style_label(prefix: str) -> tuple[str, str]:
    values = {
        "- ": ("bold red", "YOURS"),
        "+ ": ("bold green", "DUPE"),
    }
    return values.get(prefix, ("bold white", "MATCH"))


def _log_comparison_item(item: dict[str, str]) -> bool:
    prefix = item["prefix"]
    content = item["content"]
    style, label = _comparison_style_label(prefix)
    symbol = prefix.strip() or " "
    logger.info(f"[{style}][{symbol}] {label.ljust(10)}: {content}[/{style}]")
    return prefix != "  "


def _render_comparison_details(results: list[dict[str, str]]) -> bool:
    results.sort(key=sorting_priority)
    detected_changes = False
    for item in results:
        if _log_comparison_item(item):
            detected_changes = True
    return detected_changes


def _log_different_disc_notice(tracker_name: str, has_changes: bool) -> None:
    if not has_changes or tracker_name not in {"LST", "AITHER"}:
        return
    logger.info(
        f"[green]{tracker_name} allows uploads for different BD discs.[/green]"
    )


def _comparison_summary(stats: dict[str, int], release_name: str) -> str:
    add_val = f"+{stats['+ ']}".ljust(3)
    rem_val = f"-{stats['- ']}".ljust(3)
    diff_summary = (
        f"[bold green]{add_val}[/bold green] [bold red]{rem_val}[/bold red]"
    )
    has_diff = bool(stats["+ "] or stats["- "])
    status_icon = " " if has_diff else "[yellow]⚠  [/yellow]"
    return f"{diff_summary} | {status_icon}{release_name}"


def compare_bdinfo(
    meta: Meta, entry: dict[str, Any], tracker_name: str
) -> tuple[str, str]:
    release_name = str(entry.get("name", "") or "")
    duplicate_content = has_bdinfo_content(entry)
    source_lines, target_lines = get_relevant_lines(meta, duplicate_content)
    results, stats = _comparison_results(source_lines, target_lines)

    logger.info(f"\n[bold yellow]RELEASE:[/bold yellow] {release_name}")
    logger.info("[dim]Comparison Details:[/dim]\n")

    has_detected_changes = _render_comparison_details(results)
    warning_message = generate_warning(
        release_name, duplicate_content, has_detected_changes
    )
    _log_different_disc_notice(tracker_name, has_detected_changes)
    return warning_message, _comparison_summary(stats, release_name)


def generate_warning(
    release_name: str, has_content: str, has_changes: bool
) -> str:
    """
    Generates user-friendly warning messages based on the comparison state.
    """
    if not has_content:
        return f"[yellow]⚠  Warning[/yellow] for dupe [bold green]{release_name}[/bold green]: [red]No BDInfo found![/red]"
    if not has_changes:
        return f"[red]⚠  Warning[/red] for dupe [bold green]{release_name}[/bold green]: [red]No differences found.[/red]"
    return ""


def load_bdinfo_file(meta: Meta) -> tuple[str, str]:
    """
    Reads summary and extended summary files from the temporary metadata directory.
    """
    base_path = Path(meta.base_dir) / "tmp" / meta.uuid

    def read_file(name: str) -> str:
        file_path = base_path / name
        return (
            file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        )

    return read_file("BD_SUMMARY_00.txt"), read_file("BD_SUMMARY_EXT_00.txt")


def _description_has_bdinfo_marker(description: str) -> bool:
    markers = ("Disc Title:", "Disc Label:", "Disc Size: ")
    return any(marker in description for marker in markers)


def has_bdinfo_content(entry: dict[str, Any]) -> str:
    """
    Attempts to locate BDInfo content within an entry's fields.
    """
    content = str(entry.get("bd_info", "") or "")
    if content:
        return content
    description = str(entry.get("description", "") or "")
    return description if _description_has_bdinfo_marker(description) else ""


def remove_formatting(content: str) -> str:
    """
    Strips BBCode and HTML tags from the provided string.
    """
    content = re.sub(r"(?i)<br\s*/?>", "\n", content)
    content = re.sub(r"(?i)</p\s*>", "\n", content)
    content = re.sub(BBCODE_PATTERN, "", content)
    return re.sub(HTML_PATTERN, "", content)


def sorting_priority(item: dict[str, str]) -> tuple[int, str]:
    """
    Determines the display order of differences (Video first, then General, then Subtitles).
    """
    content = item["content"].lower()
    if "fps" in content:
        priority = 0
    elif any(x in content for x in ("subtitle", "presentation graphics")):
        priority = 2
    else:
        priority = 1
    return priority, content
