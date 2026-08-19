"""Pure redaction and JSON serialization policies."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import aiofiles

from src.domain_models.release import Meta

SENSITIVE_KEYS: set[str] = {
    "token",
    "passkey",
    "password",
    "auth",
    "cookie",
    "csrf",
    "email",
    "username",
    "user",
    "key",
    "info_hash",
    "AntiCsrfToken",
    "torrent_pass",
    "Popcron",
}


class PathAwareEncoder(json.JSONEncoder):
    """JSON encoder that converts pathlib.Path objects to strings."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Path):
            return str(o)
        return super().default(o)


class Redaction:
    @staticmethod
    def extract_json_blocks(text: str) -> list[tuple[int, int]]:
        """Extract JSON-like blocks from a string using bracket counting.

        Returns a list of (start, end) slices where `text[start:end]` is a candidate JSON
        object (`{...}`) or array (`[...]`). This supports *nested* JSON by tracking a
        bracket stack, and ignores brackets that occur inside quoted strings.

        Notes / limitations:
        - This is a best-effort extractor for embedded JSON substrings.
        - It does not attempt to support non-standard JSON (JSON5, trailing commas, etc.).
        - Blocks are only redacted if `json.loads` successfully parses them.
        """
        blocks: list[tuple[int, int]] = []
        stack: list[str] = []
        start: int | None = None
        in_string = False
        string_char: str | None = None
        escape = False
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if in_string:
                if ch == "\\":
                    escape = True
                elif ch == string_char:
                    in_string = False
                    string_char = None
                continue
            if ch == '"':
                in_string = True
                string_char = ch
                continue
            if ch in ("{", "["):
                if not stack:
                    start = i
                stack.append(ch)
                continue
            if ch in ("}", "]") and stack:
                top = stack[-1]
                if (ch == "}" and top == "{") or (ch == "]" and top == "["):
                    stack.pop()
                    if not stack and start is not None:
                        blocks.append((start, i + 1))
                        start = None
        return blocks

    @staticmethod
    def redact_value(val: Any, sensitive_keys: set[str] | None = None) -> Any:
        """Redact sensitive values, including passkeys in URLs and JSON substrings."""
        keys = sensitive_keys or SENSITIVE_KEYS
        if isinstance(val, str):
            blocks = Redaction.extract_json_blocks(val)
            for start, end in reversed(blocks):
                json_str = val[start:end]
                try:
                    parsed = json.loads(json_str)
                except json.JSONDecodeError, TypeError:
                    continue
                try:
                    redacted = Redaction.redact_private_info(parsed, keys)
                    redacted_str = json.dumps(redacted)
                except TypeError, ValueError:
                    continue
                val = val[:start] + redacted_str + val[end:]
            val = re.sub("(?<=/)[a-zA-Z0-9]{10,}(?=/announce)", "[REDACTED]", val)
            val = re.sub("(?<=/proxy/)[^/]+(?=/api)", "[REDACTED]", val)
            val = re.sub("([?&](passkey|key|token|api_key|auth|info_hash|torrent_pass)=)[^&]+", "\\1[REDACTED]", val, flags=re.I)
            val = re.sub("\\b[a-fA-F0-9]{32,}\\b", "[REDACTED]", val)
        return val

    @staticmethod
    def redact_private_info(data: Any, sensitive_keys: set[str] | None = None) -> Any:
        """Recursively redact sensitive info in dicts/lists/strings containing JSON."""
        keys = sensitive_keys or SENSITIVE_KEYS
        if isinstance(data, dict):
            typed_data = cast(dict[str, Any], data)
            return {k: "[REDACTED]" if any(s.lower() in k.lower() for s in keys) else Redaction.redact_private_info(v, keys) for k, v in typed_data.items()}
        if isinstance(data, list):
            return [Redaction.redact_private_info(item, keys) for item in cast(list[Any], data)]
        if isinstance(data, str):
            try:
                parsed_json = json.loads(data)
                redacted_json = Redaction.redact_private_info(parsed_json, keys)
                return json.dumps(redacted_json)
            except json.JSONDecodeError, TypeError:
                return Redaction.redact_value(data, keys)
        return data

    @staticmethod
    async def clean_meta_for_export(meta: Meta) -> Meta:
        """Remove transient secrets/status and persist the sanitized metadata."""

        tracker_status = meta.tracker_status
        if tracker_status:
            typed_status = cast(dict[str, dict[str, Any]], tracker_status)
            for tracker in list(typed_status):
                typed_status[tracker].pop("status_message", None)

        meta.pop("torrent_comments", None)
        meta.pop("matched_episode_ids", None)

        output_path = Path(meta.base_dir) / "tmp" / meta.uuid / "meta.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        export_data = meta.to_dict()
        export_data.pop("archive_password", None)
        async with aiofiles.open(output_path, "w", encoding="utf-8") as handle:
            await handle.write(json.dumps(export_data, indent=4, cls=PathAwareEncoder))
        return meta


def extract_json_blocks(text: str) -> list[tuple[int, int]]:
    return Redaction.extract_json_blocks(text)


def redact_value(val: Any) -> Any:
    return Redaction.redact_value(val)


def redact_private_info(data: Any, sensitive_keys: set[str] | None = None) -> Any:
    return Redaction.redact_private_info(data, sensitive_keys)


async def clean_meta_for_export(meta: Meta) -> Meta:
    return await Redaction.clean_meta_for_export(meta)
