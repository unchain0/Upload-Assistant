"""Pure redaction and JSON serialization policies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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

_CLOSING_BRACKETS = {"}": "{", "]": "["}


@dataclass
class _JsonBlockScanner:
    blocks: list[tuple[int, int]] = field(default_factory=list)
    stack: list[str] = field(default_factory=list)
    start: int | None = None
    in_string: bool = False
    escape: bool = False

    def _consume_string_char(self, ch: str) -> bool:
        if self.escape:
            self.escape = False
            return True
        if not self.in_string:
            return False
        if ch == "\\":
            self.escape = True
        elif ch == '"':
            self.in_string = False
        return True

    def _start_string(self, ch: str) -> bool:
        if ch != '"':
            return False
        self.in_string = True
        return True

    def _push_opening_bracket(self, index: int, ch: str) -> bool:
        if ch not in {"{", "["}:
            return False
        if not self.stack:
            self.start = index
        self.stack.append(ch)
        return True

    def _matches_closing_bracket(self, ch: str) -> bool:
        expected_opening = _CLOSING_BRACKETS.get(ch)
        return (
            expected_opening is not None
            and bool(self.stack)
            and self.stack[-1] == expected_opening
        )

    def _pop_closing_bracket(self, index: int, ch: str) -> None:
        if not self._matches_closing_bracket(ch):
            return
        self.stack.pop()
        if self.stack:
            return
        if self.start is None:
            return
        self.blocks.append((self.start, index + 1))
        self.start = None

    def consume(self, index: int, ch: str) -> None:
        if self._consume_string_char(ch):
            return
        if self._start_string(ch):
            return
        if self._push_opening_bracket(index, ch):
            return
        self._pop_closing_bracket(index, ch)


class PathAwareEncoder(json.JSONEncoder):
    """JSON encoder that converts pathlib.Path objects to strings."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Path):
            return str(o)
        return super().default(o)


class Redaction:
    @staticmethod
    def extract_json_blocks(text: str) -> list[tuple[int, int]]:
        """Extract balanced JSON-like object/array slices from embedded text."""
        scanner = _JsonBlockScanner()
        for index, ch in enumerate(text):
            scanner.consume(index, ch)
        return scanner.blocks

    @staticmethod
    def _redacted_json_block(
        json_str: str, sensitive_keys: set[str]
    ) -> str | None:
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError, TypeError:
            return None
        try:
            redacted = Redaction.redact_private_info(parsed, sensitive_keys)
            return json.dumps(redacted)
        except TypeError, ValueError:
            return None

    @staticmethod
    def _redact_embedded_json(value: str, sensitive_keys: set[str]) -> str:
        for start, end in reversed(Redaction.extract_json_blocks(value)):
            redacted = Redaction._redacted_json_block(
                value[start:end], sensitive_keys
            )
            if redacted is None:
                continue
            value = value[:start] + redacted + value[end:]
        return value

    @staticmethod
    def _redact_string_patterns(value: str) -> str:
        value = re.sub(
            "(?<=/)[a-zA-Z0-9]{10,}(?=/announce)", "[REDACTED]", value
        )
        value = re.sub("(?<=/proxy/)[^/]+(?=/api)", "[REDACTED]", value)
        value = re.sub(
            "([?&](passkey|key|token|api_key|auth|info_hash|torrent_pass)=)[^&]+",
            "\\1[REDACTED]",
            value,
            flags=re.I,
        )
        return re.sub("\\b[a-fA-F0-9]{32,}\\b", "[REDACTED]", value)

    @staticmethod
    def redact_value(val: Any, sensitive_keys: set[str] | None = None) -> Any:
        """Redact sensitive values, including passkeys in URLs and JSON substrings."""
        if not isinstance(val, str):
            return val
        keys = sensitive_keys or SENSITIVE_KEYS
        value = Redaction._redact_embedded_json(val, keys)
        return Redaction._redact_string_patterns(value)

    @staticmethod
    def _is_sensitive_key(key: str, sensitive_keys: set[str]) -> bool:
        lowered = key.lower()
        return any(
            sensitive.lower() in lowered for sensitive in sensitive_keys
        )

    @staticmethod
    def _redact_mapping(
        data: dict[str, Any], sensitive_keys: set[str]
    ) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if Redaction._is_sensitive_key(key, sensitive_keys):
                redacted[key] = "[REDACTED]"
                continue
            redacted[key] = Redaction.redact_private_info(
                value, sensitive_keys
            )
        return redacted

    @staticmethod
    def _redact_sequence(
        data: list[Any], sensitive_keys: set[str]
    ) -> list[Any]:
        return [
            Redaction.redact_private_info(item, sensitive_keys)
            for item in data
        ]

    @staticmethod
    def _redact_string(value: str, sensitive_keys: set[str]) -> str:
        try:
            parsed_json = json.loads(value)
        except json.JSONDecodeError, TypeError:
            return cast(str, Redaction.redact_value(value, sensitive_keys))
        redacted_json = Redaction.redact_private_info(
            parsed_json, sensitive_keys
        )
        return json.dumps(redacted_json)

    @staticmethod
    def redact_private_info(
        data: Any, sensitive_keys: set[str] | None = None
    ) -> Any:
        """Recursively redact sensitive info in dicts/lists/strings containing JSON."""
        keys = sensitive_keys or SENSITIVE_KEYS
        if isinstance(data, dict):
            return Redaction._redact_mapping(cast(dict[str, Any], data), keys)
        if isinstance(data, list):
            return Redaction._redact_sequence(cast(list[Any], data), keys)
        if isinstance(data, str):
            return Redaction._redact_string(data, keys)
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
            await handle.write(
                json.dumps(export_data, indent=4, cls=PathAwareEncoder)
            )
        return meta


def extract_json_blocks(text: str) -> list[tuple[int, int]]:
    return Redaction.extract_json_blocks(text)


def redact_value(val: Any) -> Any:
    return Redaction.redact_value(val)


def redact_private_info(
    data: Any, sensitive_keys: set[str] | None = None
) -> Any:
    return Redaction.redact_private_info(data, sensitive_keys)


async def clean_meta_for_export(meta: Meta) -> Meta:
    return await Redaction.clean_meta_for_export(meta)
