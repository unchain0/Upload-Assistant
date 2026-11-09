# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Catbox image-host request and response decisions."""

from urllib.parse import urlparse

CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"


def catbox_form_data(userhash: object) -> dict[str, str]:
    """Anonymous uploads omit the optional account hash."""
    data = {"reqtype": "fileupload"}
    token = str(userhash or "").strip()
    if token:
        data["userhash"] = token
    return data


def _catbox_text(text: str) -> str:
    return str(text or "").strip()


def _catbox_host_ok(parsed: object) -> bool:
    scheme = getattr(parsed, "scheme", "")
    hostname = getattr(parsed, "hostname", "")
    return scheme == "https" and hostname == "files.catbox.moe"


def catbox_accepted_url(text: str, status: int) -> str:
    """Return a files.catbox.moe URL, or an empty string when Catbox rejected it."""
    if status != 200:
        return ""
    body = _catbox_text(text)
    parsed = urlparse(body)
    if not _catbox_host_ok(parsed):
        return ""
    if not parsed.path.strip("/"):
        return ""
    return body
