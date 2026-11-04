#!/usr/bin/env python3
"""Refresh the MAM session cookie from autobrr into UA and Prowlarr.

Reads indexer settings from the autobrr sqlite database, validates the cookie
against the MAM search endpoint, and writes it only when that check returns
200. An invalid cookie never replaces a stored value. The cookie is masked in
every log line.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAM_CHECK_URL = "https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php"
COOKIE_RE = re.compile(r"(?i)(?:^|;\s*)mam_id=([^;]+)")


def mask_cookie(value: str) -> str:
    """Show only the length, never the cookie itself."""
    text = value.strip()
    if not text:
        return "(empty)"
    return f"mam_id({len(text)} chars)"


def cookie_value(raw: str) -> str:
    """Accept either a bare mam_id or a cookie header."""
    text = raw.strip()
    match = COOKIE_RE.search(text)
    return match.group(1).strip() if match else text


def read_autobrr_cookie(database: Path, indexer_id: int) -> str:
    """Return the cookie stored on one autobrr indexer."""
    query = "SELECT settings FROM indexer WHERE id = ?"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute(query, (indexer_id,)).fetchone()
    if row is None:
        raise LookupError(f"autobrr indexer {indexer_id} was not found")
    settings = json.loads(row[0] or "{}")
    cookie = str(settings.get("cookie", "")).strip()
    if not cookie:
        raise LookupError(f"autobrr indexer {indexer_id} has no cookie")
    return cookie_value(cookie)


def cookie_is_valid(
    cookie: str, opener: urllib.request.OpenerDirector
) -> bool:
    """True only for HTTP 200. Any other result, including 403, is invalid."""
    request = urllib.request.Request(
        MAM_CHECK_URL,
        data=b'{"tor":{"text":"","srchIn":["title"]},"perpage":1}',
        headers={
            "Content-Type": "application/json",
            "Cookie": f"mam_id={cookie}",
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=20) as response:
            return int(response.status) == 200
    except urllib.error.HTTPError:
        return False


def replace_config_value(source: str, key: str, value: str) -> str:
    """Replace one quoted config value without rewriting the file."""
    pattern = re.compile(
        rf'(["\']{re.escape(key)}["\']\s*:\s*)(["\'])(?:\\.|[^\\])*?\2'
    )
    match = pattern.search(source)
    if match is None:
        raise LookupError(f"{key} was not found in the config")
    quoted = json.dumps(value)
    return source[: match.start(2)] + quoted + source[match.end(2) :]


def write_ua_cookie(config_path: Path, cookie: str) -> None:
    source = config_path.read_text(encoding="utf-8")
    updated = replace_config_value(source, "mam_api_key", cookie)
    if updated == source:
        return
    config_path.write_text(updated, encoding="utf-8")


def _prowlarr_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    api_key: str,
    payload: bytes | None,
) -> dict:
    if not url.startswith(("http://", "https://")):
        raise ValueError("prowlarr url must be http or https")
    request = urllib.request.Request(  # noqa: S310
        url,
        data=payload,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        method="PUT" if payload is not None else "GET",
    )
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def write_prowlarr_cookie(
    base_url: str,
    api_key: str,
    indexer_id: int,
    cookie: str,
    opener: urllib.request.OpenerDirector,
) -> None:
    url = f"{base_url.rstrip('/')}/api/v1/indexer/{indexer_id}"
    indexer = _prowlarr_json(opener, url, api_key, None)
    fields = indexer.get("fields")
    if not isinstance(fields, list):
        raise LookupError("prowlarr indexer has no fields")
    for field in fields:
        if isinstance(field, dict) and field.get("name") == "mamId":
            field["value"] = cookie
            break
    else:
        raise LookupError("prowlarr indexer has no mamId field")
    _prowlarr_json(opener, url, api_key, json.dumps(indexer).encode("utf-8"))


def sync_cookie(
    *,
    database: Path,
    autobrr_indexer: int,
    config_path: Path | None,
    prowlarr_url: str,
    prowlarr_key: str,
    prowlarr_indexer: int,
    check_only: bool,
    opener: urllib.request.OpenerDirector,
) -> int:
    """Validate, then update both consumers. Returns 0, 1, or 2."""
    cookie = read_autobrr_cookie(database, autobrr_indexer)
    print(f"autobrr cookie: {mask_cookie(cookie)}", file=sys.stderr)
    if not cookie_is_valid(cookie, opener):
        print(
            "autobrr cookie is invalid; nothing was changed", file=sys.stderr
        )
        return 2
    print("autobrr cookie is valid", file=sys.stderr)
    if check_only:
        return 0
    if config_path is not None:
        write_ua_cookie(config_path, cookie)
        print(f"updated {config_path}", file=sys.stderr)
    if prowlarr_url and prowlarr_key:
        write_prowlarr_cookie(
            prowlarr_url, prowlarr_key, prowlarr_indexer, cookie, opener
        )
        print(f"updated prowlarr indexer {prowlarr_indexer}", file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autobrr-db", type=Path, required=True)
    parser.add_argument("--autobrr-indexer", type=int, default=6)
    parser.add_argument("--ua-config", type=Path)
    parser.add_argument("--prowlarr-url", default="")
    parser.add_argument("--prowlarr-key", default="")
    parser.add_argument("--prowlarr-indexer", type=int, default=33)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return sync_cookie(
            database=args.autobrr_db,
            autobrr_indexer=args.autobrr_indexer,
            config_path=args.ua_config,
            prowlarr_url=args.prowlarr_url,
            prowlarr_key=args.prowlarr_key,
            prowlarr_indexer=args.prowlarr_indexer,
            check_only=args.check,
            opener=urllib.request.build_opener(),
        )
    except (
        LookupError,
        sqlite3.Error,
        OSError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as error:
        print(f"mam cookie sync failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
