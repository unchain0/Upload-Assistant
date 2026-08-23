# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import errno
import os
import platform
import shutil
import ssl
import time
import traceback
import xmlrpc.client  # nosec B411 - Secured with defusedxml.xmlrpc.monkey_patch() below
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import bencode
import defusedxml.xmlrpc
from torf import Torrent

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.torrent_clients.path_utils import (
    coerce_str_list,
    is_path_under,
    map_save_path,
    tracker_directory,
)

# Secure XML-RPC client using defusedxml to prevent XML attacks
defusedxml.xmlrpc.monkey_patch()


bencode_any = cast(Any, bencode)
_bencode_bread = cast(Callable[[str], dict[str, Any]], bencode_any.bread)
_bencode_bencode = cast(Callable[[Any], bytes], bencode_any.bencode)
_bencode_bwrite = cast(Callable[[Any, str], None], bencode_any.bwrite)


@dataclass(frozen=True)
class _LinkContext:
    path: str
    tracker_dir: str | Path | None
    dst: str | Path
    filelist: list[str]
    use_symlink: bool
    use_hardlink: bool


@dataclass(frozen=True)
class _MappedResume:
    path: str
    fr_file: str
    modified_fr: bool
    path_dir: str


class RtorrentClientMixin:
    config: dict[str, Any]

    async def is_valid_torrent(
        self,
        meta: Meta,
        torrent_path: str,
        torrenthash: str,
        torrent_client: str,
        client: dict[str, Any],
    ) -> tuple[bool, str]:
        raise NotImplementedError

    def _extract_tracker_ids_from_comment(
        self, comment: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _preferred_source(filelist: list[str], meta: Meta) -> str | None:
        if len(filelist) != 1:
            return meta.path
        if meta.keep_folder:
            return meta.path
        if Path(filelist[0]).is_file():
            return filelist[0]
        return meta.path

    @classmethod
    def _source_path(cls, meta: Meta) -> tuple[list[str], str]:
        filelist = coerce_str_list(meta.filelist)
        source = cls._preferred_source(filelist, meta)
        if source:
            return filelist, str(source)
        error_msg = "[red]No source path found in meta."
        logger.info(f"[bold red]{error_msg}")
        raise ValueError(error_msg)

    @staticmethod
    def _linking_flags(client: dict[str, Any]) -> tuple[bool, bool]:
        linking_method = client.get("linking")
        logger.debug(f"Linking method: {linking_method}")
        return linking_method == "symlink", linking_method == "hardlink"

    @staticmethod
    def _unix_source_drive(src: str, linked_folders: list[str]) -> str:
        src_parts = src.strip("/").split("/")
        if not src_parts:
            return "/"
        root = "/" + src_parts[0]
        for folder in linked_folders:
            if root in folder or folder in root:
                return root
        return "/"

    @classmethod
    def _source_drive(
        cls, src: str, linked_folders: list[str], is_windows: bool
    ) -> str:
        if is_windows:
            return os.path.splitdrive(src)[0]
        return cls._unix_source_drive(src, linked_folders)

    @staticmethod
    def _windows_link_target(
        src_drive: str, linked_folders: list[str]
    ) -> str | None:
        for folder in linked_folders:
            if os.path.splitdrive(folder)[0] == src_drive:
                return folder
        return None

    @staticmethod
    def _unix_link_target(
        src: str, src_drive: str, linked_folders: list[str]
    ) -> str | None:
        for folder in linked_folders:
            if (
                src.startswith(folder)
                or folder.startswith(src)
                or folder.startswith(src_drive)
            ):
                return folder
        return None

    @classmethod
    def _link_target(
        cls,
        src: str,
        src_drive: str,
        linked_folders: list[str],
        is_windows: bool,
        use_symlink: bool,
    ) -> str | None:
        target = (
            cls._windows_link_target(src_drive, linked_folders)
            if is_windows
            else cls._unix_link_target(src, src_drive, linked_folders)
        )
        if target is None and use_symlink and linked_folders:
            return linked_folders[0]
        return target

    def _tracker_link_destination(
        self, src: str, link_target: str, tracker: str
    ) -> tuple[str | Path, Path]:
        trackers = cast(dict[str, Any], self.config.get("TRACKERS", {}))
        tracker_cfg = cast(dict[str, Any], trackers.get(tracker.upper(), {}))
        link_dir_name = str(tracker_cfg.get("link_dir_name", "")).strip()
        tracker_dir = tracker_directory(link_target, link_dir_name, tracker)
        Path(tracker_dir).mkdir(parents=True, exist_ok=True)
        logger.debug(
            f"[bold yellow]Linking to tracker directory: {tracker_dir}"
        )
        logger.debug(f"[cyan]Source path: {src}")
        src_name = Path(src.rstrip(os.sep)).name
        return tracker_dir, Path(tracker_dir) / src_name

    @staticmethod
    def _hardlink_file(src: str | Path, dst: str | Path) -> None:
        try:
            os.link(src, dst)
            logger.debug(f"[green]Hard link created: {dst} -> {src}")
        except OSError as exc:
            logger.info(f"[yellow]Hard link failed: {exc}")
            logger.info(f"[yellow]Falling back to file copy for: {src}")
            shutil.copy2(src, dst)
            logger.info(f"[green]File copied instead: {dst}")

    @staticmethod
    def _hardlink_directory_file(
        src_file: Path, dst_file: Path, meta: Meta, index: int
    ) -> None:
        try:
            os.link(src_file, dst_file)
            if meta.debug and index == 0:
                logger.info(
                    f"[green]Hard link created for file: {dst_file} -> {src_file}"
                )
        except OSError as exc:
            logger.info(
                f"[yellow]Hard link failed for file {src_file.name}: {exc}"
            )
            shutil.copy2(src_file, dst_file)
            logger.info(f"[yellow]File copied instead: {dst_file}")

    @classmethod
    def _hardlink_directory(cls, src: str, dst: Path, meta: Meta) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        for root, _, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dst_dir = dst if rel_path == "." else dst / rel_path
            dst_dir.mkdir(parents=True, exist_ok=True)
            for index, filename in enumerate(files):
                src_file = Path(root) / filename
                cls._hardlink_directory_file(
                    src_file, dst_dir / filename, meta, index
                )
        logger.debug(f"[green]Directory structure and files processed: {dst}")

    @classmethod
    def _hardlink_source(cls, src: str, dst: Path, meta: Meta) -> None:
        try:
            if Path(src).is_file():
                cls._hardlink_file(src, dst)
                return
            cls._hardlink_directory(src, dst, meta)
        except OSError as exc:
            logger.info(f"[bold red]Failed to create link: {exc}")
            if meta.debug:
                logger.debug(
                    f"[yellow]Source: {src} (exists: {Path(src).exists()})"
                )
                logger.debug(f"[yellow]Destination: {dst}")
            logger.info(
                "[yellow]Continuing with rTorrent addition despite linking failure"
            )

    @staticmethod
    def _symlink_source(src: str, dst: Path, is_windows: bool) -> None:
        try:
            if is_windows:
                dst.symlink_to(src, target_is_directory=Path(src).is_dir())
            else:
                dst.symlink_to(src)
            logger.debug(f"[green]Symbolic link created: {dst} -> {src}")
        except OSError as exc:
            logger.info(f"[bold red]Failed to create symlink: {exc}")
            logger.info(
                "[yellow]Continuing with rTorrent addition despite linking failure"
            )

    @classmethod
    def _create_link(
        cls,
        src: str,
        dst: Path,
        meta: Meta,
        use_symlink: bool,
        use_hardlink: bool,
        is_windows: bool,
    ) -> None:
        if dst.exists() or dst.is_symlink():
            logger.debug(
                f"[yellow]Skipping linking, path already exists: {dst}"
            )
            return
        if use_hardlink:
            cls._hardlink_source(src, dst, meta)
            return
        if use_symlink:
            cls._symlink_source(src, dst, is_windows)

    def _link_context(
        self, path: str, meta: Meta, client: dict[str, Any], tracker: str
    ) -> _LinkContext:
        filelist, src = self._source_path(meta)
        use_symlink, use_hardlink = self._linking_flags(client)
        if not use_symlink and not use_hardlink:
            return _LinkContext(path, None, path, filelist, False, False)
        linked_folders = coerce_str_list(client.get("linked_folder", []))
        logger.debug(f"Linked folders: {linked_folders}")
        is_windows = platform.system() == "Windows"
        src_drive = self._source_drive(src, linked_folders, is_windows)
        link_target = self._link_target(
            src, src_drive, linked_folders, is_windows, use_symlink
        )
        logger.debug(f"Source drive: {src_drive}")
        logger.debug(f"Link target: {link_target}")
        if link_target is None:
            error_msg = (
                f"No suitable linked folder found for drive {src_drive}"
            )
            logger.info(f"[bold red]{error_msg}")
            raise ValueError(error_msg)
        tracker_dir, dst = self._tracker_link_destination(
            src, link_target, tracker
        )
        self._create_link(
            src, dst, meta, use_symlink, use_hardlink, is_windows
        )
        resolved_path = str(dst) if dst.exists() else path
        return _LinkContext(
            resolved_path,
            tracker_dir,
            dst,
            filelist,
            use_symlink,
            use_hardlink,
        )

    @staticmethod
    def _mapped_save_path(
        context: _LinkContext, local_path: str, remote_path: str
    ) -> str:
        save_path = (
            str(context.tracker_dir)
            if context.tracker_dir is not None
            else context.path
        )
        mapped = map_save_path(save_path, local_path, remote_path)
        logger.debug(f"[cyan]Original path: {context.path}")
        logger.debug(f"[cyan]Mapped save path: {mapped}")
        return mapped

    @staticmethod
    def _rpc_metainfo(
        client: dict[str, Any], torrent_path: str
    ) -> tuple[Any, dict[str, Any]]:
        rtorrent = xmlrpc.client.Server(
            client["rtorrent_url"], context=ssl.create_default_context()
        )
        metainfo = _bencode_bread(torrent_path)
        logger.debug(
            f"rtorrent: {Redaction.redact_private_info(str(rtorrent))}",
            extra={"markup": False},
        )
        logger.debug(
            f"metainfo: {Redaction.redact_private_info(str(metainfo))}",
            extra={"markup": False},
        )
        return rtorrent, metainfo

    @staticmethod
    def _effective_resume_path(context: _LinkContext) -> str:
        dst = Path(context.dst)
        if dst.exists():
            return str(dst)
        return context.path

    def _fast_resume_file(
        self,
        context: _LinkContext,
        metainfo: dict[str, Any],
        torrent: Torrent,
        torrent_path: str,
    ) -> str:
        original_meta_bytes = _bencode_bencode(metainfo)
        resume_path = self._effective_resume_path(context)
        logger.debug(f"[cyan]Using resume path: {resume_path}")
        try:
            fast_resume = self.add_fast_resume(metainfo, resume_path, torrent)
        except OSError as exc:
            logger.error(f"[red]Error making fast-resume data ({exc})")
            raise
        if _bencode_bencode(fast_resume) == original_meta_bytes:
            return torrent_path
        fr_file = torrent_path.replace(".torrent", "-resume.torrent")
        logger.debug(f"Creating fast resume file: {fr_file}")
        _bencode_bwrite(fast_resume, fr_file)
        return fr_file

    @staticmethod
    def _needs_remote_mapping(
        path: str, local_path: str, remote_path: str
    ) -> bool:
        if not is_path_under(path, local_path):
            return False
        return os.path.normcase(local_path) != os.path.normcase(remote_path)

    @staticmethod
    def _use_parent_path(
        meta: Meta, filelist: list[str], is_dir: bool
    ) -> bool:
        if not is_dir:
            return True
        return meta.category in ("BOOK", "GAME") and len(filelist) > 1

    @classmethod
    def _mapped_resume(
        cls,
        context: _LinkContext,
        fr_file: str,
        meta: Meta,
        local_path: str,
        remote_path: str,
    ) -> _MappedResume:
        path = cls._effective_resume_path(context)
        is_dir = Path(path).is_dir()
        modified_fr = False
        path_dir = ""
        if cls._needs_remote_mapping(path, local_path, remote_path):
            path_dir = str(Path(path).parent)
            path = map_save_path(
                path, local_path, remote_path, trailing_slash=False
            )
            shutil.copy(fr_file, f"{path_dir}/fr.torrent")
            fr_file = f"{Path(path).parent.as_posix()}/fr.torrent"
            modified_fr = True
            logger.debug(
                f"[cyan]Modified fast resume file path because path mapping: {fr_file}"
            )
        if cls._use_parent_path(meta, context.filelist, is_dir):
            path = Path(path).parent.as_posix()
        logger.debug(f"[cyan]Final path for rTorrent: {path}")
        return _MappedResume(str(path), fr_file, modified_fr, path_dir)

    @staticmethod
    def _load_rtorrent(rtorrent: Any, mapped: _MappedResume) -> None:
        logger.info("[bold yellow]Adding and starting torrent")
        rtorrent.load.start_verbose(
            "", mapped.fr_file, f"d.directory_base.set={mapped.path}"
        )
        logger.debug(
            f"[green]rTorrent load start for {mapped.fr_file} with d.directory_base.set={mapped.path}"
        )
        time.sleep(1)

    @staticmethod
    def _apply_rtorrent_labels(
        rtorrent: Any,
        torrent: Torrent,
        meta: Meta,
        client: dict[str, Any],
    ) -> None:
        client_label = client.get("rtorrent_label")
        if client_label is not None:
            logger.debug(f"[cyan]Setting rTorrent label: {client_label}")
            rtorrent.d.custom1.set(torrent.infohash, client_label)
        if meta.rtorrent_label is not None:
            rtorrent.d.custom1.set(torrent.infohash, meta.rtorrent_label)
            logger.debug(
                f"[cyan]Setting rTorrent label from meta: {meta.rtorrent_label}"
            )

    @staticmethod
    def _cleanup_mapped_resume(mapped: _MappedResume) -> None:
        if not mapped.modified_fr:
            return
        logger.debug(
            f"[cyan]Removing modified fast resume file: {mapped.fr_file}"
        )
        try:
            Path(f"{mapped.path_dir}/fr.torrent").unlink()
        except OSError as exc:
            logger.debug(
                f"[yellow]Warning: Could not remove modified fast resume file: {exc}[/yellow]"
            )

    def rtorrent(
        self,
        path: str,
        torrent_path: str,
        torrent: Torrent,
        meta: Meta,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        tracker: str,
    ) -> None:
        context = self._link_context(path, meta, client, tracker)
        self._mapped_save_path(context, local_path, remote_path)
        rtorrent, metainfo = self._rpc_metainfo(client, torrent_path)
        fr_file = self._fast_resume_file(
            context, metainfo, torrent, torrent_path
        )
        mapped = self._mapped_resume(
            context, fr_file, meta, local_path, remote_path
        )
        self._load_rtorrent(rtorrent, mapped)
        self._apply_rtorrent_labels(rtorrent, torrent, meta, client)
        self._cleanup_mapped_resume(mapped)
        logger.debug(f"[cyan]Path: {mapped.path}")

    @staticmethod
    def _piece_length(metainfo: dict[str, Any]) -> int:
        value = metainfo["info"]["piece length"]
        piece_length = (
            int(value) if isinstance(value, (int, float, str)) else 0
        )
        if piece_length <= 0:
            raise ValueError(f"Invalid piece length: {value!r}")
        return piece_length

    @staticmethod
    def _resume_files(
        metainfo: dict[str, Any], datapath: str
    ) -> tuple[list[dict[str, Any]], bool, str]:
        files = metainfo["info"].get("files")
        if files is not None:
            return cast(list[dict[str, Any]], files), False, datapath
        resolved_datapath = datapath
        if Path(resolved_datapath).is_dir():
            resolved_datapath = str(
                Path(resolved_datapath) / metainfo["info"]["name"]
            )
        single_file: dict[str, Any] = {
            "path": [str(Path(resolved_datapath).resolve())],
            "length": metainfo["info"]["length"],
        }
        return [single_file], True, resolved_datapath

    @staticmethod
    def _resume_filepath(
        fileinfo: dict[str, Any], datapath: str, single: bool
    ) -> Path:
        filepath = Path(*fileinfo["path"])
        if single:
            return filepath
        return Path(datapath) / str(filepath).strip(os.sep)

    @staticmethod
    def _file_length(fileinfo: dict[str, Any]) -> int:
        value = fileinfo["length"]
        return int(value) if isinstance(value, (int, float, str)) else 0

    @staticmethod
    def _validate_file_size(
        filepath: Path, file_length: int
    ) -> os.stat_result:
        file_stat = filepath.stat()
        if file_stat.st_size != file_length:
            raise OSError(
                errno.EINVAL,
                f"File size mismatch for {str(filepath)!r} [is {file_stat.st_size}, expected {file_length}]",
            )
        return file_stat

    @staticmethod
    def _completed_pieces(
        offset: int, file_length: int, piece_length: int
    ) -> int:
        return (
            offset + file_length + piece_length - 1
        ) // piece_length - offset // piece_length

    @classmethod
    def _resume_file_entry(
        cls,
        fileinfo: dict[str, Any],
        datapath: str,
        single: bool,
        offset: int,
        piece_length: int,
    ) -> tuple[dict[str, int], int]:
        filepath = cls._resume_filepath(fileinfo, datapath, single)
        file_length = cls._file_length(fileinfo)
        file_stat = cls._validate_file_size(filepath, file_length)
        entry = {
            "priority": 1,
            "mtime": int(file_stat.st_mtime),
            "completed": cls._completed_pieces(
                offset, file_length, piece_length
            ),
        }
        return entry, offset + file_length

    def add_fast_resume(
        self, metainfo: dict[str, Any], datapath: str, _torrent: Torrent
    ) -> dict[str, Any]:
        """Add fast resume data to a metafile dict."""
        files, single, datapath = self._resume_files(metainfo, datapath)
        resume = metainfo.setdefault("libtorrent_resume", {})
        resume["bitfield"] = len(metainfo["info"]["pieces"]) // 20
        resume["files"] = []
        piece_length = self._piece_length(metainfo)
        offset = 0
        for fileinfo in files:
            entry, offset = self._resume_file_entry(
                fileinfo, datapath, single, offset, piece_length
            )
            resume["files"].append(entry)
        return metainfo

    def _default_rtorrent_client(self) -> dict[str, Any] | None:
        default_cfg = cast(dict[str, Any], self.config.get("DEFAULT", {}))
        default_client = default_cfg.get("default_torrent_client")
        if not isinstance(default_client, str) or not default_client:
            logger.info("[yellow]Missing default torrent client for rTorrent")
            return None
        clients_cfg = cast(
            dict[str, Any], self.config.get("TORRENT_CLIENTS", {})
        )
        return cast(dict[str, Any], clients_cfg.get(default_client, {}))

    def _resolved_rtorrent_client(
        self, client: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if client is not None:
            return client
        return self._default_rtorrent_client()

    @staticmethod
    def _valid_storage_hash_inputs(
        storage: str | None, info_hash: str | None, path: str | None
    ) -> bool:
        return bool(storage and info_hash and path)

    @classmethod
    def _storage_and_hash(
        cls, meta: Meta, client: dict[str, Any]
    ) -> tuple[str, str] | None:
        storage_value = client.get("torrent_storage_dir")
        storage = storage_value if isinstance(storage_value, str) else None
        hash_value = meta.infohash
        info_hash = hash_value if isinstance(hash_value, str) else None
        if not cls._valid_storage_hash_inputs(storage, info_hash, meta.path):
            logger.info(
                "[yellow]Missing torrent storage directory, infohash, or meta path"
            )
            return None
        return cast(str, storage), cast(str, info_hash).upper().strip()

    @staticmethod
    def _extracted_torrent_dir(meta: Meta) -> Path:
        if not meta.uuid:
            meta.uuid = Path(str(meta.path)).name
        extracted_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        extracted_dir.mkdir(parents=True, exist_ok=True)
        return extracted_dir

    @staticmethod
    def _case_insensitive_torrent(
        storage_dir: Path, info_hash: str
    ) -> Path | None:
        if not storage_dir.exists():
            return None
        for candidate in storage_dir.iterdir():
            if not candidate.name.lower().endswith(".torrent"):
                continue
            if candidate.stem.upper() == info_hash:
                logger.info(
                    f"[green]Found torrent file with matching hash: {candidate.name}"
                )
                return candidate
        return None

    @classmethod
    def _torrent_path(cls, storage: str, info_hash: str) -> Path | None:
        storage_dir = Path(storage)
        direct = storage_dir / f"{info_hash}.torrent"
        if direct.exists():
            logger.info(f"[green]Found matching torrent file: {direct}")
            return direct
        logger.info(
            f"[yellow]Searching for torrent file with hash {info_hash} in {storage}"
        )
        found = cls._case_insensitive_torrent(storage_dir, info_hash)
        if found is None:
            logger.info(
                f"[bold red]No torrent file found for hash: {info_hash}"
            )
        return found

    @staticmethod
    def _torrent_comments(meta: Meta) -> list[dict[str, Any]]:
        value = meta.torrent_comments
        source = value if isinstance(value, list) else []
        comments = [
            cast(dict[str, Any], entry)
            for entry in source
            if isinstance(entry, dict)
        ]
        meta.torrent_comments = comments
        return comments

    @staticmethod
    def _comment_data(torrent: Torrent, comment: str) -> dict[str, Any]:
        return {
            "hash": getattr(torrent, "infohash_v1", "") or "",
            "name": getattr(torrent, "name", "") or "",
            "comment": comment,
        }

    def _record_torrent_comment(
        self, meta: Meta, torrent: Torrent, comment: str
    ) -> None:
        comments = self._torrent_comments(meta)
        comments.append(self._comment_data(torrent, comment))
        logger.debug(f"[cyan]Stored comment for torrent: {comment[:100]}...")
        tracker_ids = self._extract_tracker_ids_from_comment(comment)
        meta.set_tracker_ids(tracker_ids)
        for tracker_name, torrent_id in tracker_ids.items():
            logger.info(
                f"[bold cyan]meta updated with {tracker_name.upper()} ID: {torrent_id}"
            )
        if comments and meta.debug:
            logger.info(
                f"[green]Stored {len(comments)} torrent comments for later use"
            )

    @staticmethod
    def _copy_base_torrent(
        resolved_path: str, base_torrent_path: Path
    ) -> None:
        try:
            shutil.copy2(resolved_path, base_torrent_path)
            logger.info(
                f"[yellow]Created simple torrent copy as fallback: {base_torrent_path}"
            )
        except Exception as copy_err:
            logger.info(f"[bold red]Failed to create backup copy: {copy_err}")

    @classmethod
    async def _create_base_torrent(
        cls, meta: Meta, resolved_path: str, extracted_dir: Path
    ) -> None:
        base_torrent_path = extracted_dir / "BASE.torrent"
        try:
            await TorrentCreator.create_base_from_existing_torrent(
                resolved_path, meta.base_dir, meta.uuid
            )
            logger.debug("[green]Created BASE.torrent from existing torrent")
        except Exception as exc:
            logger.info(f"[bold red]Error creating BASE.torrent: {exc}")
            cls._copy_base_torrent(resolved_path, base_torrent_path)

    async def _ensure_base_torrent(
        self,
        meta: Meta,
        torrent_path: Path,
        info_hash: str,
        client: dict[str, Any],
        extracted_dir: Path,
        pathed: bool,
    ) -> None:
        if pathed:
            return
        valid, resolved_path = await self.is_valid_torrent(
            meta, str(torrent_path), info_hash, "rtorrent", client
        )
        if valid:
            await self._create_base_torrent(meta, resolved_path, extracted_dir)

    async def _process_rtorrent_file(
        self,
        meta: Meta,
        torrent_path: Path,
        info_hash: str,
        client: dict[str, Any],
        extracted_dir: Path,
        pathed: bool,
    ) -> None:
        try:
            torrent = Torrent.read(torrent_path)
            comment = torrent.comment or ""
            logger.debug(f"[cyan]Torrent comment: {comment}")
            self._record_torrent_comment(meta, torrent, comment)
            await self._ensure_base_torrent(
                meta,
                torrent_path,
                info_hash,
                client,
                extracted_dir,
                pathed,
            )
        except Exception as exc:
            logger.info(f"[bold red]Error reading torrent file: {exc}")
            logger.info(f"[dim]{traceback.format_exc()}[/dim]")

    async def get_ptp_from_hash_rtorrent(
        self,
        meta: Meta,
        pathed: bool = False,
        client: dict[str, Any] | None = None,
    ) -> Meta:
        resolved_client = self._resolved_rtorrent_client(client)
        if resolved_client is None:
            return meta
        storage_hash = self._storage_and_hash(meta, resolved_client)
        if storage_hash is None:
            return meta
        storage, info_hash = storage_hash
        extracted_dir = self._extracted_torrent_dir(meta)
        torrent_path = self._torrent_path(storage, info_hash)
        if torrent_path is None:
            return meta
        await self._process_rtorrent_file(
            meta,
            torrent_path,
            info_hash,
            resolved_client,
            extracted_dir,
            pathed,
        )
        return meta
