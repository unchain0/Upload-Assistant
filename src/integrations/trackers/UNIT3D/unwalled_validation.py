import hashlib
import re
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import bencodepy
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger

bdecode = cast(Callable[[bytes], object], vars(bencodepy)["decode"])


class UnwalledValidationMixin:
    tracker: str

    @staticmethod
    def _has_symlink_component(path: Path) -> bool:
        absolute = path.expanduser().absolute()
        return any(
            component.is_symlink()
            for component in (*reversed(absolute.parents), absolute)
        )

    @classmethod
    def _valid_filename(cls, name: str) -> bool:
        if not cls._filename_shape_is_valid(name):
            return False
        if cls._filename_has_forbidden_characters(name):
            return False
        if cls._is_padding_name(name):
            return False
        return cls._filename_stem(name) not in cls._reserved_names()

    @staticmethod
    def _filename_shape_is_valid(name: str) -> bool:
        if not name:
            return False
        if len(name.encode("utf-8")) > 255:
            return False
        if name != name.strip():
            return False
        if set(name) == {"."}:
            return False
        return not name.endswith(".")

    @staticmethod
    def _filename_has_forbidden_characters(name: str) -> bool:
        return re.search(r"[\/?<>:*|\x00-\x1f]", name) is not None

    @staticmethod
    def _is_padding_name(name: str) -> bool:
        lowered = name.casefold()
        return lowered.startswith(".pad") or lowered.startswith("____padding")

    @staticmethod
    def _filename_stem(name: str) -> str:
        return name.split(".", 1)[0].upper()

    @staticmethod
    def _reserved_names() -> set[str]:
        return {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }

    @classmethod
    def _image_details(
        cls, path_value: str
    ) -> tuple[Path, str, tuple[int, int]] | None:
        path = Path(path_value)
        if not cls._image_path_is_safe(path):
            return None
        try:
            return cls._verified_image_details(path)
        except (
            OSError,
            SyntaxError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            return None

    @classmethod
    def _image_path_is_safe(cls, path: Path) -> bool:
        if cls._has_symlink_component(path) or not path.is_file():
            return False
        return path.stat().st_size < 1024 * 1024

    @classmethod
    def _verified_image_details(
        cls, path: Path
    ) -> tuple[Path, str, tuple[int, int]] | None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = str(image.format or "")
                image_size = image.size
                if not cls._image_dimensions_are_safe(image_size):
                    return None
                image.verify()
                return path, image_format, image_size

    @staticmethod
    def _image_dimensions_are_safe(size: tuple[int, int]) -> bool:
        width, height = size
        return (
            width <= 3840 and height <= 3840 and width * height <= 16_000_000
        )

    @classmethod
    def _valid_torrent_paths(cls, meta: Meta) -> bool:
        root = Path(str(meta.path or ""))
        prepared = cls._prepared_root(root)
        if prepared is None:
            return False
        base, resolved_base = prepared
        if not cls._valid_root_name(root):
            return False
        return all(
            cls._valid_file_path(base, resolved_base, value)
            for value in meta.filelist
        )

    @classmethod
    def _valid_root_name(cls, root: Path) -> bool:
        if not root.is_dir():
            return True
        return cls._valid_filename(root.name)

    @classmethod
    def _prepared_root(cls, root: Path) -> tuple[Path, Path] | None:
        if cls._has_symlink_component(root) or not root.exists():
            return None
        base = root if root.is_dir() else root.parent
        try:
            return base, base.resolve(strict=True)
        except OSError:
            return None

    @classmethod
    def _valid_file_path(
        cls, base: Path, resolved_base: Path, file_value: object
    ) -> bool:
        file_path = Path(str(file_value))
        if cls._has_symlink_component(file_path) or not file_path.is_file():
            return False
        relative_path = cls._relative_file_path(file_path, resolved_base)
        if relative_path is None:
            return False
        return cls._valid_relative_components(base, relative_path)

    @staticmethod
    def _relative_file_path(
        file_path: Path, resolved_base: Path
    ) -> Path | None:
        try:
            return file_path.resolve(strict=True).relative_to(resolved_base)
        except OSError, ValueError:
            return None

    @classmethod
    def _valid_relative_components(
        cls, base: Path, relative_path: Path
    ) -> bool:
        current_path = base
        for component in relative_path.parts:
            current_path /= component
            if current_path.is_symlink() or not cls._valid_filename(component):
                return False
        return True

    @classmethod
    def _valid_announce_url(cls, value: str) -> bool:
        try:
            parsed = urlsplit(value)
            token = parsed.path.removeprefix("/announce/")
            return cls._valid_announce_parts(parsed, token)
        except ValueError:
            return False

    @staticmethod
    def _valid_announce_parts(parsed: object, token: str) -> bool:
        checks = (
            getattr(parsed, "scheme", "") == "https",
            getattr(parsed, "hostname", None) == "unwalled.cc",
            getattr(parsed, "port", None) in (None, 443),
            getattr(parsed, "username", None) is None,
            getattr(parsed, "password", None) is None,
            getattr(parsed, "query", "") == "",
            getattr(parsed, "fragment", "") == "",
            getattr(parsed, "path", "") == f"/announce/{token}",
            re.fullmatch(r"[A-Za-z0-9_-]+", token) is not None,
        )
        return all(checks)

    @classmethod
    def _torrent_metainfo(
        cls, path: Path
    ) -> tuple[dict[bytes, object], dict[bytes, object]] | None:
        if not cls._torrent_path_is_safe(path):
            return None
        decoded = cls._decode_torrent(path)
        if not isinstance(decoded, dict):
            return None
        metainfo = cast(dict[bytes, object], decoded)
        raw_info = metainfo.get(b"info")
        if not isinstance(raw_info, dict):
            return None
        return metainfo, cast(dict[bytes, object], raw_info)

    @staticmethod
    def _torrent_path_is_safe(path: Path) -> bool:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size < 1024 * 1024
        )

    @staticmethod
    def _decode_torrent(path: Path) -> object | None:
        try:
            return bdecode(path.read_bytes())
        except (
            OSError,
            RecursionError,
            ValueError,
            bencodepy.BencodeDecodeError,
        ):
            return None

    @classmethod
    def _valid_v1_info(cls, info: dict[bytes, object]) -> bool:
        header = cls._v1_header(info)
        if header is None:
            return False
        name, piece_length, pieces = header
        if not cls._valid_filename(name):
            return False
        mode = cls._torrent_content_mode(info)
        if mode == "single":
            return cls._valid_single_file_info(info, pieces, piece_length)
        if mode == "multi":
            return cls._valid_multi_file_info(info, pieces, piece_length)
        return False

    @classmethod
    def _v1_header(
        cls, info: dict[bytes, object]
    ) -> tuple[str, int, bytes] | None:
        raw_name = info.get(b"name")
        piece_length = info.get(b"piece length")
        pieces = info.get(b"pieces")
        if not isinstance(raw_name, bytes):
            return None
        if not cls._valid_piece_length(piece_length):
            return None
        if not cls._valid_piece_blob(info, pieces):
            return None
        name = cls._decode_utf8(raw_name)
        if name is None:
            return None
        return name, cast(int, piece_length), cast(bytes, pieces)

    @staticmethod
    def _valid_piece_length(value: object) -> bool:
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        if value < 16 * 1024 or value > 128 * 1024 * 1024:
            return False
        return value & (value - 1) == 0

    @staticmethod
    def _valid_piece_blob(info: dict[bytes, object], pieces: object) -> bool:
        if not isinstance(pieces, bytes) or not pieces:
            return False
        if len(pieces) % 20 != 0:
            return False
        return b"meta version" not in info and b"file tree" not in info

    @staticmethod
    def _decode_utf8(value: bytes) -> str | None:
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _torrent_content_mode(info: dict[bytes, object]) -> str:
        has_length = b"length" in info
        has_files = b"files" in info
        if has_length == has_files:
            return ""
        return "single" if has_length else "multi"

    @classmethod
    def _valid_single_file_info(
        cls, info: dict[bytes, object], pieces: bytes, piece_length: int
    ) -> bool:
        length = info.get(b"length")
        if not cls._valid_file_length(length):
            return False
        return cls._piece_count_matches(
            pieces, piece_length, cast(int, length)
        )

    @staticmethod
    def _valid_file_length(value: object) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        )

    @classmethod
    def _valid_multi_file_info(
        cls, info: dict[bytes, object], pieces: bytes, piece_length: int
    ) -> bool:
        raw_files = cls._raw_file_list(info)
        if not raw_files:
            return False
        total_length = cls._valid_files_total_length(raw_files)
        if total_length is None:
            return False
        return cls._piece_count_matches(pieces, piece_length, total_length)

    @staticmethod
    def _raw_file_list(info: dict[bytes, object]) -> list[object]:
        value = info.get(b"files")
        return value if isinstance(value, list) else []

    @classmethod
    def _valid_files_total_length(cls, raw_files: list[object]) -> int | None:
        total = 0
        for raw_file in raw_files:
            parsed = cls._valid_v1_file_entry(raw_file)
            if parsed is None:
                return None
            total += parsed[1]
        return total

    @classmethod
    def _valid_v1_file_entry(
        cls, raw_file: object
    ) -> tuple[tuple[str, ...], int] | None:
        entry = cls._torrent_entry_mapping(raw_file)
        return None if entry is None else cls._complete_v1_file_entry(entry)

    @classmethod
    def _complete_v1_file_entry(
        cls, entry: dict[bytes, object]
    ) -> tuple[tuple[str, ...], int] | None:
        length = cls._torrent_entry_length(entry)
        if length is None:
            return None
        raw_path = cls._torrent_entry_path(entry)
        if raw_path is None:
            return None
        if cls._is_symlink_torrent_entry(entry):
            return None
        path = cls._decoded_torrent_path(raw_path)
        return None if path is None else (path, length)

    @staticmethod
    def _torrent_entry_mapping(raw_file: object) -> dict[bytes, object] | None:
        return (
            cast(dict[bytes, object], raw_file)
            if isinstance(raw_file, dict)
            else None
        )

    @classmethod
    def _torrent_entry_length(cls, entry: dict[bytes, object]) -> int | None:
        value = entry.get(b"length")
        return cast(int, value) if cls._valid_file_length(value) else None

    @staticmethod
    def _torrent_entry_path(entry: dict[bytes, object]) -> list[object] | None:
        value = entry.get(b"path")
        return (
            cast(list[object], value)
            if isinstance(value, list) and value
            else None
        )

    @staticmethod
    def _is_symlink_torrent_entry(entry: dict[bytes, object]) -> bool:
        attr = entry.get(b"attr", b"")
        return isinstance(attr, bytes) and b"l" in attr

    @classmethod
    def _decoded_torrent_path(
        cls, raw_path: list[object]
    ) -> tuple[str, ...] | None:
        components: list[str] = []
        for raw_component in raw_path:
            component = cls._decoded_path_component(raw_component)
            if component is None:
                return None
            components.append(component)
        return tuple(components)

    @classmethod
    def _decoded_path_component(cls, raw_component: object) -> str | None:
        if not isinstance(raw_component, bytes):
            return None
        component = cls._decode_utf8(raw_component)
        if component is None or component in {".", ".."}:
            return None
        return component if cls._valid_filename(component) else None

    @staticmethod
    def _piece_count_matches(
        pieces: bytes, piece_length: int, total_length: int
    ) -> bool:
        return (
            len(pieces) // 20
            == (total_length + piece_length - 1) // piece_length
        )

    @classmethod
    def _torrent_matches_files(
        cls, info: dict[bytes, object], meta: Meta
    ) -> bool:
        root = Path(str(meta.path or ""))
        try:
            if not cls._torrent_root_matches(info, root):
                return False
            if root.is_file():
                return cls._single_torrent_matches(info, meta, root)
            return cls._directory_torrent_matches(info, meta, root)
        except OSError, UnicodeDecodeError, ValueError:
            return False

    @classmethod
    def _torrent_root_matches(
        cls, info: dict[bytes, object], root: Path
    ) -> bool:
        if cls._has_symlink_component(root) or not root.exists():
            return False
        raw_name = info.get(b"name")
        if not isinstance(raw_name, bytes):
            return False
        name = cls._decode_utf8(raw_name)
        return name == root.name

    @staticmethod
    def _single_torrent_matches(
        info: dict[bytes, object], meta: Meta, root: Path
    ) -> bool:
        if len(meta.filelist) != 1:
            return False
        listed = Path(str(meta.filelist[0])).resolve(strict=True)
        return (
            listed == root.resolve(strict=True)
            and info.get(b"length") == root.stat().st_size
        )

    @classmethod
    def _directory_torrent_matches(
        cls, info: dict[bytes, object], meta: Meta, root: Path
    ) -> bool:
        expected = cls._expected_files(meta, root)
        if expected is None:
            return False
        actual = cls._actual_torrent_files(info)
        return actual is not None and actual == expected

    @classmethod
    def _expected_files(
        cls, meta: Meta, root: Path
    ) -> dict[tuple[str, ...], int] | None:
        expected: dict[tuple[str, ...], int] = {}
        resolved_root = root.resolve(strict=True)
        for value in meta.filelist:
            path = Path(str(value))
            if not path.is_file() or cls._has_symlink_component(path):
                return None
            relative = tuple(
                path.resolve(strict=True).relative_to(resolved_root).parts
            )
            expected[relative] = path.stat().st_size
        return expected

    @classmethod
    def _actual_torrent_files(
        cls, info: dict[bytes, object]
    ) -> dict[tuple[str, ...], int] | None:
        raw_files = info.get(b"files")
        if not isinstance(raw_files, list):
            return None
        actual: dict[tuple[str, ...], int] = {}
        for raw_file in raw_files:
            parsed = cls._torrent_file_entry(raw_file)
            if parsed is None:
                return None
            path, length = parsed
            if path in actual:
                return None
            actual[path] = length
        return actual

    @classmethod
    def _torrent_file_entry(
        cls, raw_file: object
    ) -> tuple[tuple[str, ...], int] | None:
        if not isinstance(raw_file, dict):
            return None
        entry = cast(dict[bytes, object], raw_file)
        raw_path = entry.get(b"path")
        length = entry.get(b"length")
        if not isinstance(raw_path, list) or not isinstance(length, int):
            return None
        path = cls._decoded_torrent_path(cast(list[object], raw_path))
        return None if path is None else (path, length)

    @classmethod
    def _torrent_is_v1(cls, path: Path) -> bool:
        torrent = cls._torrent_metainfo(path)
        return torrent is not None and cls._valid_v1_info(torrent[1])

    @staticmethod
    def _file_digest(path: Path) -> bytes:
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").digest()

    def _valid_artwork(self, meta: Meta) -> bool:
        cover = self._image_details(meta.artwork_path)
        if not self._cover_is_valid(cover):
            logger.info(
                f"{self.tracker}: [bold red]Cover must be a square JPEG of at least 400x400.[/bold red]"
            )
            return False
        banner = self._image_details(str(meta.artwork_banner_path or ""))
        if not self._banner_is_valid(banner):
            logger.info(
                f"{self.tracker}: [bold red]Banner must be a 16:9 JPEG of at least 960x540.[/bold red]"
            )
            return False
        cover_value = cast(tuple[Path, str, tuple[int, int]], cover)
        banner_value = cast(tuple[Path, str, tuple[int, int]], banner)
        if not self._artwork_is_distinct(cover_value[0], banner_value[0]):
            logger.info(
                f"{self.tracker}: [bold red]Cover and banner must be different images.[/bold red]"
            )
            return False
        return True

    @staticmethod
    def _cover_is_valid(
        details: tuple[Path, str, tuple[int, int]] | None,
    ) -> bool:
        if details is None:
            return False
        _path, image_format, (width, height) = details
        return image_format == "JPEG" and width == height and width >= 400

    @staticmethod
    def _banner_is_valid(
        details: tuple[Path, str, tuple[int, int]] | None,
    ) -> bool:
        if details is None:
            return False
        _path, image_format, (width, height) = details
        if image_format != "JPEG" or width < 960 or height < 540:
            return False
        return abs((width / height) - (16 / 9)) <= 0.03

    @classmethod
    def _artwork_is_distinct(cls, cover: Path, banner: Path) -> bool:
        if cover.resolve() == banner.resolve():
            return False
        return cls._file_digest(cover) != cls._file_digest(banner)

    def _valid_upload_bundle(self, meta: Meta, torrent_path: Path) -> bool:
        torrent = self._validated_upload_torrent(meta, torrent_path)
        if torrent is None:
            logger.info(
                f"{self.tracker}: [bold red]Unwalled requires a valid V1 torrent.[/bold red]"
            )
            return False
        if not self._upload_metadata_is_valid(meta, torrent):
            return False
        return self._upload_assets_are_valid(meta, torrent_path)

    def _upload_metadata_is_valid(
        self,
        meta: Meta,
        torrent: tuple[dict[bytes, object], dict[bytes, object]],
    ) -> bool:
        metainfo, info = torrent
        announce = self._announce_text(metainfo)
        if announce is None:
            return False
        if self._private_metadata_is_valid(meta, info, announce):
            return True
        logger.info(
            f"{self.tracker}: [bold red]The upload torrent is missing required Unwalled private metadata.[/bold red]"
        )
        return False

    def _upload_assets_are_valid(self, meta: Meta, torrent_path: Path) -> bool:
        if not self._valid_artwork(meta):
            return False
        if self._bundle_files_are_safe(meta, torrent_path):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Torrent, cover and banner must total less than 1 MiB.[/bold red]"
        )
        return False

    @classmethod
    def _validated_upload_torrent(
        cls, meta: Meta, torrent_path: Path
    ) -> tuple[dict[bytes, object], dict[bytes, object]] | None:
        torrent = cls._torrent_metainfo(torrent_path)
        if torrent is None:
            return None
        _metainfo, info = torrent
        if not cls._valid_v1_info(info):
            return None
        return torrent if cls._torrent_matches_files(info, meta) else None

    @classmethod
    def _announce_text(cls, metainfo: dict[bytes, object]) -> str | None:
        raw_announce = metainfo.get(b"announce", b"")
        if not isinstance(raw_announce, bytes):
            return ""
        return cls._decode_utf8(raw_announce)

    @classmethod
    def _private_metadata_is_valid(
        cls, meta: Meta, info: dict[bytes, object], announce: str
    ) -> bool:
        if info.get(b"private") != 1 or info.get(b"source") != b"Unwalled":
            return False
        if meta.debug:
            return announce == "https://fake.tracker"
        return cls._valid_announce_url(announce)

    @classmethod
    def _bundle_files_are_safe(cls, meta: Meta, torrent_path: Path) -> bool:
        paths = cls._bundle_paths(meta, torrent_path)
        if any(not cls._bundle_path_is_safe(path) for path in paths):
            return False
        return sum(path.stat().st_size for path in paths) < 1024 * 1024

    @staticmethod
    def _bundle_paths(
        meta: Meta, torrent_path: Path
    ) -> tuple[Path, Path, Path]:
        return (
            torrent_path,
            Path(meta.artwork_path),
            Path(str(meta.artwork_banner_path or "")),
        )

    @classmethod
    def _bundle_path_is_safe(cls, path: Path) -> bool:
        return not cls._has_symlink_component(path) and path.is_file()
