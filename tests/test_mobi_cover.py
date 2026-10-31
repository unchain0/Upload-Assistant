from __future__ import annotations

import asyncio
import struct
from pathlib import Path

from src.integrations.media.screenshot_capture import (
    _mobi_cover_bytes,
    extract_mobi_cover,
)

_JPEG = b"\xff\xd8\xff\xe0" + b"cover-bytes" * 8 + b"\xff\xd9"


def _exth(cover_offset: int | None) -> bytes:
    records = b""
    if cover_offset is not None:
        records = struct.pack(">II", 201, 12) + struct.pack(">I", cover_offset)
    count = 1 if records else 0
    return b"EXTH" + struct.pack(">II", 12 + len(records), count) + records


def _record_zero(first_image: int, cover_offset: int | None) -> bytes:
    header = bytearray(16 + 232)
    header[16:20] = b"MOBI"
    header[20:24] = struct.pack(">I", 232)
    header[0x6C:0x70] = struct.pack(">I", first_image)
    return bytes(header) + _exth(cover_offset)


def _book(records: list[bytes]) -> bytes:
    header = bytearray(78 + 8 * len(records))
    header[60:68] = b"BOOKMOBI"
    header[76:78] = struct.pack(">H", len(records))
    offset = len(header)
    body = b""
    for index, record in enumerate(records):
        header[78 + 8 * index : 82 + 8 * index] = struct.pack(">I", offset)
        offset += len(record)
        body += record
    return bytes(header) + body


def test_mobi_cover_is_extracted_from_the_exth_cover_offset() -> None:
    data = _book([_record_zero(2, 0), b"text record", _JPEG])
    assert _mobi_cover_bytes(data) == _JPEG


def test_mobi_without_cover_metadata_returns_none() -> None:
    data = _book([_record_zero(2, None), b"text", _JPEG])
    assert _mobi_cover_bytes(data) is None


def test_mobi_cover_offset_pointing_at_text_is_rejected() -> None:
    data = _book([_record_zero(1, 0), b"text", _JPEG])
    assert _mobi_cover_bytes(data) is None


def test_non_pdb_file_returns_none() -> None:
    assert _mobi_cover_bytes(b"not a mobi file" * 10) is None


def test_extract_mobi_cover_writes_the_cover(tmp_path: Path) -> None:
    source = tmp_path / "book.mobi"
    source.write_bytes(_book([_record_zero(2, 0), b"text record", _JPEG]))
    destination = tmp_path / "cover.jpg"

    assert (
        asyncio.run(extract_mobi_cover(str(source), str(destination))) is True
    )
    assert destination.read_bytes() == _JPEG


def test_extract_mobi_cover_reports_failure_without_a_cover(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.azw3"
    source.write_bytes(b"nope" * 40)
    destination = tmp_path / "cover.jpg"

    assert (
        asyncio.run(extract_mobi_cover(str(source), str(destination))) is False
    )
    assert not destination.exists()
