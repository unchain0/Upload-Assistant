from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import bencodepy
import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.unwalled import Unwalled
from tests.test_unwalled import _tracker

bencode = cast(Callable[[object], bytes], vars(bencodepy)["encode"])


def test_unwalled_filename_guard_branches() -> None:
    assert not Unwalled._valid_filename(".pad0")
    assert not Unwalled._filename_shape_is_valid("")
    assert not Unwalled._filename_shape_is_valid("x" * 256)
    assert not Unwalled._filename_shape_is_valid("..")


def test_unwalled_image_path_and_dimension_guards(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jpg"
    assert Unwalled._image_details(str(missing)) is None
    assert not Unwalled._image_path_is_safe(missing)

    large_dimensions = tmp_path / "wide.jpg"
    Image.new("RGB", (4000, 10), "white").save(large_dimensions, format="JPEG")
    assert Unwalled._verified_image_details(large_dimensions) is None


def test_unwalled_torrent_path_root_guards(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert not Unwalled._valid_torrent_paths(Meta(path=str(missing), filelist=[]))

    invalid_root = tmp_path / ".pad-folder"
    invalid_root.mkdir()
    assert not Unwalled._valid_torrent_paths(Meta(path=str(invalid_root), filelist=[]))


def test_unwalled_prepared_root_handles_resolve_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_resolve = Path.resolve

    def broken_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == root:
            raise OSError("resolve failed")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    assert Unwalled._prepared_root(root) is None


def test_unwalled_file_path_guards(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    resolved = base.resolve()
    assert not Unwalled._valid_file_path(base, resolved, base / "missing.mp3")

    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"audio")
    assert not Unwalled._valid_file_path(base, resolved, outside)
    assert Unwalled._relative_file_path(outside, resolved) is None


def test_unwalled_torrent_metainfo_guards(tmp_path: Path) -> None:
    assert Unwalled._torrent_metainfo(tmp_path / "missing.torrent") is None
    bad_info = tmp_path / "bad-info.torrent"
    bad_info.write_bytes(bencode({b"announce": b"https://fake.tracker"}))
    assert Unwalled._torrent_metainfo(bad_info) is None


def _valid_header(**extra: object) -> dict[bytes, object]:
    info: dict[bytes, object] = {b"name": b"release", b"piece length": 16384, b"pieces": b"x" * 20}
    info.update(cast(dict[bytes, object], extra))
    return info


def test_unwalled_v1_header_and_content_mode_guards() -> None:
    assert not Unwalled._valid_v1_info({**_valid_header(), b"name": b"CON", b"length": 1})
    assert not Unwalled._valid_v1_info(_valid_header())
    assert Unwalled._v1_header({**_valid_header(), b"name": b"\xff"}) is None
    assert not Unwalled._valid_piece_length(True)
    assert not Unwalled._valid_piece_blob(_valid_header(), b"")
    assert not Unwalled._valid_piece_blob(_valid_header(), b"short")
    assert Unwalled._decode_utf8(b"\xff") is None
    assert Unwalled._torrent_content_mode({b"length": 1, b"files": []}) == ""


def test_unwalled_single_and_multi_v1_guards() -> None:
    assert not Unwalled._valid_single_file_info({b"length": True}, b"x" * 20, 16384)
    assert not Unwalled._valid_multi_file_info({b"files": []}, b"x" * 20, 16384)

    assert Unwalled._complete_v1_file_entry({b"length": True, b"path": [b"a"]}) is None
    assert Unwalled._complete_v1_file_entry({b"length": 1, b"path": []}) is None
    assert Unwalled._complete_v1_file_entry({b"length": 1, b"path": [b"a"], b"attr": b"l"}) is None
    assert Unwalled._decoded_path_component("not-bytes") is None


def test_unwalled_torrent_file_matching_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing"
    assert not Unwalled._torrent_matches_files({b"name": b"missing"}, Meta(path=str(missing), filelist=[]))
    assert not Unwalled._torrent_root_matches({b"name": b"missing"}, missing)

    root = tmp_path / "root"
    root.mkdir()
    assert not Unwalled._torrent_root_matches({b"name": "wrong-type"}, root)
    assert not Unwalled._single_torrent_matches({b"length": 1}, Meta(filelist=[]), root)
    assert Unwalled._expected_files(Meta(filelist=[str(root / "missing.mp3")]), root) is None
    assert Unwalled._actual_torrent_files({b"files": "bad"}) is None
    assert Unwalled._actual_torrent_files({b"files": ["bad"]}) is None
    assert Unwalled._torrent_file_entry("bad") is None
    assert Unwalled._torrent_file_entry({b"path": "bad", b"length": 1}) is None

    monkeypatch.setattr(Unwalled, "_torrent_root_matches", classmethod(lambda _cls, _info, _root: (_ for _ in ()).throw(ValueError("bad"))))
    assert not Unwalled._torrent_matches_files({b"name": b"root"}, Meta(path=str(root), filelist=[]))


def test_unwalled_directory_match_expected_files_failure(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    meta = Meta(path=str(root), filelist=[str(root / "missing.mp3")])
    assert not Unwalled._directory_torrent_matches({b"files": []}, meta, root)


def test_unwalled_artwork_invalid_cover_and_identity_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    meta = Meta(artwork_path=str(tmp_path / "missing.jpg"), artwork_banner_path=str(tmp_path / "missing-banner.jpg"))
    assert not tracker._valid_artwork(meta)

    same = tmp_path / "same.jpg"
    same.write_bytes(b"x")
    details = iter(((same, "JPEG", (400, 400)), (same, "JPEG", (960, 540))))
    monkeypatch.setattr(tracker, "_image_details", lambda _value: next(details))
    assert not tracker._valid_artwork(meta)

    assert not tracker._cover_is_valid(None)
    assert not tracker._banner_is_valid((same, "PNG", (960, 540)))
    assert not tracker._artwork_is_distinct(same, same)


def test_unwalled_upload_bundle_helper_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    torrent = tmp_path / "upload.torrent"
    torrent.write_bytes(b"torrent")
    meta = Meta(artwork_path=str(tmp_path / "cover.jpg"), artwork_banner_path=str(tmp_path / "banner.jpg"), debug=False)

    monkeypatch.setattr(tracker, "_validated_upload_torrent", lambda *_args: ({b"announce": b"https://unwalled.cc/announce/token"}, {b"private": 1, b"source": b"Unwalled"}))
    monkeypatch.setattr(tracker, "_upload_metadata_is_valid", lambda *_args: False)
    assert not tracker._valid_upload_bundle(meta, torrent)

    invalid_announce = ({b"announce": b"\xff"}, {b"private": 1, b"source": b"Unwalled"})
    assert not tracker._upload_metadata_is_valid(meta, invalid_announce)

    bad_metadata = ({b"announce": b"https://unwalled.cc/announce/token"}, {b"private": 0, b"source": b"Unwalled"})
    assert not tracker._upload_metadata_is_valid(meta, bad_metadata)


def test_unwalled_upload_asset_and_torrent_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    torrent = tmp_path / "upload.torrent"
    torrent.write_bytes(b"torrent")
    meta = Meta(artwork_path=str(tmp_path / "cover.jpg"), artwork_banner_path=str(tmp_path / "banner.jpg"))

    monkeypatch.setattr(tracker, "_valid_artwork", lambda _meta: False)
    assert not tracker._upload_assets_are_valid(meta, torrent)

    assert tracker._validated_upload_torrent(meta, tmp_path / "missing.torrent") is None
    monkeypatch.setattr(tracker, "_torrent_metainfo", lambda _path: ({}, {}))
    assert tracker._validated_upload_torrent(meta, torrent) is None


def test_unwalled_announce_and_private_metadata_guards() -> None:
    tracker = _tracker()
    assert tracker._announce_text({b"announce": "not-bytes"}) == ""
    assert not tracker._private_metadata_is_valid(Meta(debug=False), {}, "https://unwalled.cc/announce/token")


def test_unwalled_bundle_files_reject_missing_paths(tmp_path: Path) -> None:
    meta = Meta(artwork_path=str(tmp_path / "cover.jpg"), artwork_banner_path=str(tmp_path / "banner.jpg"))
    assert not _tracker()._bundle_files_are_safe(meta, tmp_path / "missing.torrent")


def test_unwalled_banner_none_is_invalid() -> None:
    assert not Unwalled._banner_is_valid(None)


def test_unwalled_upload_metadata_invalid_announce_and_private_fields() -> None:
    tracker = _tracker()
    meta = Meta(debug=False)
    invalid_announce = ({b"announce": b"\xff"}, {b"private": 1, b"source": b"Unwalled"})
    assert not tracker._upload_metadata_is_valid(meta, invalid_announce)

    bad_metadata = ({b"announce": b"https://unwalled.cc/announce/token"}, {b"private": 0, b"source": b"Unwalled"})
    assert not tracker._upload_metadata_is_valid(meta, bad_metadata)


def test_unwalled_validated_upload_torrent_rejects_invalid_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    torrent_path = tmp_path / "upload.torrent"
    torrent_path.write_bytes(b"x")
    monkeypatch.setattr(Unwalled, "_torrent_metainfo", classmethod(lambda _cls, _path: ({}, {})))
    assert tracker._validated_upload_torrent(Meta(), torrent_path) is None
