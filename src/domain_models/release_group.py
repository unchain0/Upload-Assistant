# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Release-group value semantics shared across detection and upload safety."""

import re

# Bounded numeric widths and anchored alternatives make this expression linear-time.
_EPISODE_TOKEN_RE = re.compile(  # nosemgrep: regex_dos
    r"^(?:s\d{1,3}(?:e\d{1,4})*|e\d{1,4}|\d{1,3}x\d{1,4}|ep\d{1,4}|episode\d{1,4})$",
    re.IGNORECASE,
)
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE)
_PREFIX_METADATA_RE = re.compile(
    r"(?:^|[._\s-])(?:"
    r"\d{3,4}[pi]|"
    r"web(?:[._\s-]?dl|rip)?|"
    r"blu[._\s-]?ray|bdrip|remux|"
    r"h[._\s-]?26[45]|x26[45]|hevc|avc|"
    r"aac|flac|ddp?|dts(?:[._\s-]?hd)?|"
    r"dual[._\s-]?audio|d[._\s-]?sub"
    r")(?:$|[._\s-])",
    re.IGNORECASE,
)


def release_group_name(value: object) -> str:
    """Return a release-group value without the metadata tag delimiter."""
    text = str(value or "").strip()
    return text[1:].strip() if text.startswith("-") else text


def release_group_has_episode_syntax(value: object) -> bool:
    """Return whether a candidate is actually season/episode syntax."""
    group = release_group_name(value).strip(" _-")
    compact = re.sub(r"[^A-Za-z0-9]+", "", group)
    return bool(compact and _EPISODE_TOKEN_RE.fullmatch(compact))


def is_valid_release_group(value: object) -> bool:
    """Return whether a non-empty release group is semantically upload-safe."""
    group = release_group_name(value)
    return bool(group) and not release_group_has_episode_syntax(group)


def is_valid_prefixed_release_group(value: object) -> bool:
    """Return whether an initial bracket token is plausible as a release group."""
    group = release_group_name(value).strip(" _-")
    if not is_valid_release_group(group) or _CHECKSUM_RE.fullmatch(group):
        return False
    return _PREFIX_METADATA_RE.search(group) is None
