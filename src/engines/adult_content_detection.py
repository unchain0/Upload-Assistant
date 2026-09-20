import re
from collections.abc import Iterable

from src.domain_models.adult_keywords import XXX_METADATA_KEYWORDS
from src.domain_models.adult_platforms import XXX_PLATFORM_KEYWORDS

_WORD_RE = re.compile(r"[a-z0-9]+")
_KEYWORD_TOKENS: dict[tuple[str, ...], str] = {}
for _keyword in sorted(XXX_PLATFORM_KEYWORDS | XXX_METADATA_KEYWORDS):
    _KEYWORD_TOKENS.setdefault(tuple(_WORD_RE.findall(_keyword)), _keyword)
_MAX_KEYWORD_TOKENS = max(map(len, _KEYWORD_TOKENS))


def extract_xxx_keywords(
    release_name: str, existing_keywords: Iterable[str] | str | None = None
) -> list[str]:
    """Add known XXX platform and descriptive tags found in a release name."""
    existing = _normalized_existing_keywords(existing_keywords)
    tokens = _WORD_RE.findall(release_name.casefold())
    matches = _candidate_keyword_matches(tokens)
    selected = _select_non_overlapping_matches(matches)
    return _append_unseen_keywords(existing, selected)


def _normalized_existing_keywords(
    existing_keywords: Iterable[str] | str | None,
) -> list[str]:
    values = (
        existing_keywords.split(",")
        if isinstance(existing_keywords, str)
        else (existing_keywords or ())
    )
    return [text for keyword in values if (text := str(keyword).strip())]


def _candidate_keyword_matches(
    tokens: list[str],
) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    for start in range(len(tokens)):
        matches.extend(_matches_at_position(tokens, start))
    return sorted(matches, key=_match_priority)


def _matches_at_position(
    tokens: list[str], start: int
) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    maximum = min(_MAX_KEYWORD_TOKENS, len(tokens) - start)
    for size in range(1, maximum + 1):
        keyword = _KEYWORD_TOKENS.get(tuple(tokens[start : start + size]))
        if keyword:
            matches.append((start, start + size, keyword))
    return matches


def _match_priority(match: tuple[int, int, str]) -> tuple[int, int, int, str]:
    start, end, keyword = match
    return -(end - start), -len(keyword), start, keyword


def _select_non_overlapping_matches(
    matches: list[tuple[int, int, str]],
) -> list[tuple[int, str]]:
    selected: list[tuple[int, str]] = []
    occupied: set[int] = set()
    for start, end, keyword in matches:
        positions = set(range(start, end))
        if occupied.isdisjoint(positions):
            occupied.update(positions)
            selected.append((start, keyword))
    return selected


def _append_unseen_keywords(
    existing: list[str], matched: list[tuple[int, str]]
) -> list[str]:
    seen = {keyword.casefold() for keyword in existing}
    for _, keyword in sorted(matched):
        folded = keyword.casefold()
        if folded not in seen:
            existing.append(keyword)
            seen.add(folded)
    return existing
