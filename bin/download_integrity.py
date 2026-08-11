"""Pinned SHA-256 verification for third-party executable downloads."""

import asyncio
import hashlib
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any

MAX_ASSET_BYTES = 128 * 1024 * 1024
TRANSFER_TIMEOUT_SECONDS = 120.0
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024

SHA256_BY_ASSET = {
    "mkbrr_1.24.0_windows_x86_64.zip": "23b923a26d50e3afabcd99938ea70a510904a98365f698bbeaae057ec1a51711",
    "mkbrr_1.24.0_darwin_arm64.tar.gz": "99c939d1b3e7329d1cf82c90adceb93291eefc34f72ca263577b9c4826172c1e",
    "mkbrr_1.24.0_darwin_x86_64.tar.gz": "26092fa6b59ec79ede4be8d09f9f6f3135af1ccf0f4df312215b2ed92691e7dd",
    "mkbrr_1.24.0_linux_x86_64.tar.gz": "bd23b2e2aa62a943eb5cfea23fa250e60b9ba2169a36e27aaeec980d82dc47a5",
    "mkbrr_1.24.0_linux_arm64.tar.gz": "dada7d9aab0bd0854cdf3b5473b77d7f04b7dcf99ef519b45b522959f9db78e7",
    "mkbrr_1.24.0_linux_arm.tar.gz": "7480a96b9dafd458be1769d8c743bc85c53034a9673252b831b3130e8c1b758d",
    "mkbrr_1.24.0_freebsd_x86_64.tar.gz": "d3c2fe3bc9bad2467faa38124f7f1ebf3e70b8b32300486ee21f0667bd7fc6c7",
    "mkbrr_1.18.0_linux_x86_64.tar.gz": "a796bd97dfb093e18a1a509c8986580498e65253582983a462b977b359f987b9",
    "mkbrr_1.18.0_linux_arm64.tar.gz": "1c187ab2b860e637296d6f0deb4c2e7754a4c1e249b0226f0be671170689de24",
    "mkbrr_1.18.0_linux_arm.tar.gz": "f622595f6afee302c72c89abdd9f31ad3197bd85d45a8b482f97ebd21930ac51",
    "bdinfo_0.3.1_windows_amd64.zip": "53258982e0aee24f87a95d6869552512028217f5ce3c77ac0036cf0eed1f3073",
    "bdinfo_0.3.1_darwin_amd64.tar.gz": "8d63e3fd4cff3d3438c40bcf8c006114388820bb3f118c67b25bd1b18b73ed1f",
    "bdinfo_0.3.1_darwin_arm64.tar.gz": "31b414300a5745acaacbd99a4ebc5ce2623aa7614986f41be51570c712f692ba",
    "bdinfo_0.3.1_linux_amd64.tar.gz": "75cffac8adf3c1c971aaffb7843edd053a2d275814439bfd4868ba7774080feb",
    "bdinfo_0.3.1_linux_arm64.tar.gz": "37d389107894589a54e37e0d9f503c8f8c53960614714ee8095f06bdb9ec9437",
    "bdinfo_0.3.1_linux_arm.tar.gz": "21e382e063cf81d9c3b97adfa92ae841e9d23caac7045c63beec0a9e0b1fe82e",
    "26.01/7zr.exe": "abcf64ae1cbafddb5395e4cdd3bdc7e3e0561d54a0c6380e3dd43bdbffe519a2",
    "7z2601-mac.tar.xz": "0b6b930dbf82742e3f1014c35072a6b8b3aab183fece348e7f723675f1c5bea2",
    "7z2601-linux-x64.tar.xz": "8ea0fc8a135e7b848e80a4116fe22dff56c8c4518dde1f43cce67f4e340b437a",
    "7z2601-linux-arm64.tar.xz": "39f8c9070c300a63c7484d9a983119ef3edf841e1ddf69f1affae29fdec5f612",
    "7z2601-linux-arm.tar.xz": "72c19911abb6964fcf85ebe213dfcee57bad892345e03bb940c5a27a1050b3bf",
    "pesto-v0.6.0/pesto-windows-x86_64.exe": "25306273eb6ed91abfb465d9a3e60adceb868883f7c456926d2de4eee1e57236",
    "pesto-v0.6.0/pesto-linux-x86_64": "4a357e37c5f867694fa95d025a7aba836d43f6cfa410f1ab78e86f817f4c656c",
    "par2cmdline-turbo-1.4.0-win-x64.zip": "7905d1d6aced2b2ca30d824b4954e6bf740dc9d6cfec718b0ab146b5fc0d6327",
    "par2cmdline-turbo-1.4.0-win-arm64.zip": "89870943c142b1360ab79bb287e47d5bffc686e159581750917207bbad2f6dcb",
    "par2cmdline-turbo-1.4.0-macos-arm64.zip": "926139d3cf18f6c4e4aeb25d6fc12b758cdf4936788fb46acd18caf21ffa9a15",
    "par2cmdline-turbo-1.4.0-macos-amd64.zip": "29ebb3629911a5b3ce4cdd8723a551a2877771b633630f443d99637559ef76be",
    "par2cmdline-turbo-1.4.0-linux-amd64.zip": "0be495172b4b8aeabda39c493e47de652813fab88ae745c8633e901c05494281",
    "par2cmdline-turbo-1.4.0-linux-arm64.zip": "1bb2acb2c549bb3a2e91be3ac6291b00d4b657a56ab23f763f2161ffe7df0fcd",
    "nyuu-v0.4.2-win32.7z": "b14eb105e064ec2bd8f6d872c1c652a9943ea54d767c80554617f4eff6c801b8",
    "nyuu-v0.4.2-linux-aarch64.tar.xz": "8a94f3f775996e4469736494074ac7663ff463748b0e302c2bc13d0ff4a88c0b",
    "nyuu-v0.4.2-linux-amd64.tar.xz": "bbea69ffaf1d8ed3465935157e3842fe7a38bade2703504879eb8bc7c0a83dff",
    "nyuu-v0.4.2-macos-x64.tar.xz": "040c56a486bc4ac7e3b0eed7a482ffce1bbf747ff731ad45ffd99d7230fcb2a0",
    "MediaInfo_CLI_23.04_Lambda_x86_64.zip": "a3874e3387085075bfb9900fe6cea7899e7bda5b33f741ebdcf9020caf21325f",
    "MediaInfo_DLL_23.04_Lambda_x86_64.zip": "2a4674dc79d24568838a582f8fb55bed48014cff7566685b1f3f19bb3f0a8714",
    "MediaInfo_CLI_23.04_Lambda_arm64.zip": "8d47d63a36dde47070dcb0eb2e0726e4d339be57bcdd0e56f8153c81d2e47a63",
    "MediaInfo_DLL_23.04_Lambda_arm64.zip": "fb5bb11ecbc73f69ef8f017bb05ff56a167b0a813a4fbf2f974d4f20f3eb9f93",
}


def _safe_destination(base: Path, member_name: str) -> Path:
    member_path = Path(member_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise RuntimeError(f"Unsafe archive member: {member_name}")
    destination = (base / member_path).resolve()
    if destination != base and base not in destination.parents:
        raise RuntimeError(f"Archive member escapes destination: {member_name}")
    return destination


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path, *, max_bytes: int = MAX_EXTRACTED_BYTES) -> None:
    """Extract regular ZIP members with path, type and expanded-size limits."""
    base = destination.resolve()
    total = 0
    for member in archive.infolist():
        target = _safe_destination(base, member.filename)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"Archive links are not allowed: {member.filename}")
        if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeError(f"Unsupported archive member: {member.filename}")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        total += member.file_size
        if member.file_size > max_bytes or total > max_bytes:
            raise RuntimeError(f"Archive exceeds the {max_bytes}-byte expanded-size limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def extract_zip_regular_member(archive: zipfile.ZipFile, member_name: str, destination: Path, *, max_bytes: int = MAX_EXTRACTED_BYTES) -> None:
    """Copy one regular ZIP member to a fixed destination with an expanded-size limit."""
    member = archive.getinfo(member_name)
    mode = member.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if member.is_dir() or stat.S_ISLNK(mode) or (file_type and not stat.S_ISREG(mode)):
        raise RuntimeError(f"Archive member is not a regular file: {member_name}")
    if member.file_size > max_bytes:
        raise RuntimeError(f"Archive member exceeds the {max_bytes}-byte expanded-size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, destination.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def safe_extract_tar(archive: tarfile.TarFile, destination: Path, *, max_bytes: int = MAX_EXTRACTED_BYTES) -> None:
    """Extract regular TAR members with path, type and expanded-size limits."""
    base = destination.resolve()
    total = 0
    for member in archive.getmembers():
        target = _safe_destination(base, member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise RuntimeError(f"Unsupported archive member: {member.name}")
        total += member.size
        if member.size > max_bytes or total > max_bytes:
            raise RuntimeError(f"Archive exceeds the {max_bytes}-byte expanded-size limit")
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"Unable to read archive member: {member.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def promote_files_with_rollback(
    replacements: list[tuple[Path, Path]],
    backup_dir: Path,
    *,
    remove_targets: list[Path] | None = None,
) -> None:
    """Promote staged files together, restoring every previous target on failure."""
    if backup_dir.exists():
        raise RuntimeError(f"Recovery backup must be resolved before another update: {backup_dir}")
    backup_dir.mkdir(parents=True)
    backups: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    promotion_succeeded = False
    targets = [target for _source, target in replacements]
    targets.extend(remove_targets or [])
    target_names = [target.name for target in targets]
    if len(target_names) != len(set(target_names)):
        raise ValueError("Transactional promotion targets must have unique filenames")
    try:
        for target in targets:
            if target.exists():
                backup = backup_dir / target.name
                target.replace(backup)
                backups.append((backup, target))
        for source, target in replacements:
            source.replace(target)
            promoted.append(target)
        promotion_succeeded = True
    except BaseException as promotion_error:
        rollback_errors: list[BaseException] = []
        for target in reversed(promoted):
            try:
                target.unlink(missing_ok=True)
            except BaseException as error:
                rollback_errors.append(error)
        for backup, target in reversed(backups):
            try:
                backup.replace(target)
            except BaseException as error:
                rollback_errors.append(error)
        if rollback_errors:
            raise BaseExceptionGroup(
                "File promotion failed and rollback was incomplete",
                [promotion_error, *rollback_errors],
            ) from promotion_error
        raise
    finally:
        if promotion_succeeded or not any(backup.exists() for backup, _target in backups):
            shutil.rmtree(backup_dir, ignore_errors=True)


def verify_downloaded_asset(path: Path, asset: str) -> None:
    """Fail closed unless a downloaded executable archive matches its pinned hash."""
    expected = SHA256_BY_ASSET.get(asset)
    if expected is None:
        raise RuntimeError(f"No SHA-256 checksum is pinned for {asset}")
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(f"SHA-256 checksum mismatch for {asset}")


def sha256_file(path: Path) -> str:
    """Hash a file incrementally without requiring newer hashlib helpers."""
    digest = hashlib.sha256()
    with path.open("rb") as asset_file:
        for chunk in iter(lambda: asset_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_declared_size(response: Any, max_bytes: int) -> None:
    declared = response.headers.get("content-length")
    if declared is None:
        return
    try:
        declared_size = int(declared)
    except (TypeError, ValueError):
        return
    if declared_size > max_bytes:
        raise RuntimeError(f"Download exceeds the {max_bytes}-byte limit")


async def download_bounded_asset(
    client: Any,
    url: str,
    destination: Path,
    *,
    max_bytes: int = MAX_ASSET_BYTES,
    timeout_seconds: float = TRANSFER_TIMEOUT_SECONDS,
) -> None:
    """Stream a response to disk with declared, running-size and total-time limits."""
    destination.unlink(missing_ok=True)

    async def transfer() -> None:
        async with client.stream("GET", url, timeout=min(timeout_seconds, 60.0)) as response:
            response.raise_for_status()
            _validate_declared_size(response, max_bytes)
            received = 0
            with destination.open("wb") as output:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    received += len(chunk)
                    if received > max_bytes:
                        raise RuntimeError(f"Download exceeds the {max_bytes}-byte limit")
                    output.write(chunk)

    try:
        await asyncio.wait_for(transfer(), timeout=timeout_seconds)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


async def download_verified_asset(client: Any, url: str, destination: Path, asset: str) -> None:
    """Download a bounded asset and fail closed unless its pinned digest matches."""
    try:
        await download_bounded_asset(client, url, destination)
        verify_downloaded_asset(destination, asset)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def download_bounded_asset_sync(
    url: str,
    destination: Path,
    *,
    max_bytes: int = MAX_ASSET_BYTES,
    timeout_seconds: float = TRANSFER_TIMEOUT_SECONDS,
) -> None:
    """Run the bounded async transfer for synchronous bootstrap entry points."""
    import httpx

    async def download() -> None:
        async with httpx.AsyncClient(timeout=min(timeout_seconds, 60.0), follow_redirects=True) as client:
            await download_bounded_asset(client, url, destination, max_bytes=max_bytes, timeout_seconds=timeout_seconds)

    asyncio.run(download())


def download_verified_asset_sync(url: str, destination: Path, asset: str) -> None:
    """Synchronous bounded download with pinned checksum verification."""
    try:
        download_bounded_asset_sync(url, destination)
        verify_downloaded_asset(destination, asset)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
