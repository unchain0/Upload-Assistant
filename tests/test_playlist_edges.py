from __future__ import annotations

import io
import struct

import pytest

from src.integrations.runtime_tools.playlist import MplsParser, load_movie_playlist, load_playlist


def test_movie_playlist_header_wrapper_repr_and_nonzero_position() -> None:
    payload = b"MPLS" + b"0200" + struct.pack(">III", 40, 80, 120) + (b"\0" * 20)
    stream = io.BytesIO(payload)
    header = load_movie_playlist(stream)  # type: ignore[arg-type]
    assert header.type_indicator == "MPLS"
    assert header.version_number == "0200"
    assert (header.playlist_start_address, header.playlist_mark_start_address, header.extension_data_start_address) == (40, 80, 120)

    parser = MplsParser(io.BytesIO(payload))  # type: ignore[arg-type]
    assert "MplsParser" not in repr(parser)
    parser.mpls.seek(1)
    with pytest.raises(ValueError, match="start of the mpls"):
        parser.load_movie_playlist()


def test_playlist_zero_length_and_empty_play_item() -> None:
    stream = io.BytesIO(struct.pack(">I", 0))
    playlist = load_playlist(stream)  # type: ignore[arg-type]
    assert playlist.length == 0
    assert playlist.nb_play_items is None
    assert playlist.play_items is None
    assert stream.tell() == 4

    # A zero-length PlayItem still advances past its two-byte length field.
    parser = MplsParser(io.BytesIO(struct.pack(">H", 0)))  # type: ignore[arg-type]
    item = parser._load_play_item()
    assert item.length == 0
    assert item.clip_information_filename is None
    assert item.intime is None and item.outtime is None
    assert parser.mpls.tell() == 2


def test_playlist_with_play_item_parses_clip_times_and_seeks_to_boundary() -> None:
    play_item_payload = struct.pack(">H", 20) + b"00001" + b"M2TS" + b"\0\0" + b"\0" + struct.pack(">I", 90_000) + struct.pack(">I", 180_000)
    playlist_body = b"\0\0" + struct.pack(">H", 1) + struct.pack(">H", 0) + play_item_payload
    payload = struct.pack(">I", len(playlist_body)) + playlist_body
    stream = io.BytesIO(payload)

    playlist = load_playlist(stream)  # type: ignore[arg-type]

    assert playlist.length == len(playlist_body)
    assert playlist.nb_play_items == 1
    assert playlist.nb_sub_paths == 0
    assert len(playlist.play_items) == 1
    item = playlist.play_items[0]
    assert item.clip_information_filename == "00001"
    assert item.intime == 90_000
    assert item.outtime == 180_000
    assert stream.tell() == len(payload)


def test_unpack_rejects_unsupported_width() -> None:
    parser = MplsParser(io.BytesIO(b"\0" * 16))  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        parser._unpack_byte(3)
