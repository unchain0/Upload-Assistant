"""Well-known per-release temporary asset directories.

The release temporary directory also stores metadata, torrents and logs.  Image
artifacts must not share that root: consumers often enumerate PNGs and would
otherwise mistake a poster, cover or diagnostic image for a screenshot.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _is_trusted_shared_root(path: Path) -> bool:
    attributes = path.stat()
    required_mode = stat.S_ISVTX | stat.S_IWOTH | stat.S_IXOTH
    return attributes.st_uid == 0 and stat.S_ISDIR(attributes.st_mode) and attributes.st_mode & required_mode == required_mode


def ensure_temp_root(base_dir: str | Path) -> Path:
    path = Path(base_dir) / "tmp"
    if path.is_symlink():
        raise RuntimeError(f"Temporary root must not be a symbolic link: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except PermissionError:
            if not _is_trusted_shared_root(path):
                raise
    return path


def release_temp_dir(base_dir: str | Path, release_id: str) -> Path:
    """Return the root temporary directory for one release."""
    return Path(base_dir) / "tmp" / str(release_id)


def image_dir(base_dir: str | Path, release_id: str, kind: str) -> Path:
    """Return and create a typed image directory below a release's temp root."""
    path = release_temp_dir(base_dir, release_id) / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshots_dir(base_dir: str | Path, release_id: str) -> Path:
    return image_dir(base_dir, release_id, "screenshots")


def artwork_dir(base_dir: str | Path, release_id: str) -> Path:
    """Return the per-release directory for all local artwork assets."""
    return image_dir(base_dir, release_id, "artwork")


def menu_screenshots_dir(base_dir: str | Path, release_id: str) -> Path:
    return image_dir(base_dir, release_id, "menu_screenshots")


def spectrograms_dir(base_dir: str | Path, release_id: str) -> Path:
    return image_dir(base_dir, release_id, "spectrograms")


def dynamic_hdr_plots_dir(base_dir: str | Path, release_id: str) -> Path:
    """Return the per-release directory for Dolby Vision/HDR10+ plot images."""
    return image_dir(base_dir, release_id, "dynamic_hdr_plots")
