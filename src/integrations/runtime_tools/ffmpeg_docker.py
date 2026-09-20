# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import shutil
from pathlib import Path

from src.integrations.observability.console import logger


class FfmpegBinaryManager:
    @staticmethod
    def download_ffmpeg_for_docker(base_dir: str | Path = ".") -> str:
        """Download ffmpeg amd and arm builds and install into bin/ffmpeg/<arch>/ffmpeg.

        This is a synchronous helper intended for use in Dockerfile build steps.
        """
        # Use platform.system() for a well-typed string
        system = platform.system().lower()
        logger.info(f"[blue]Detected system: {system}[/blue]")

        if "linux" not in system:
            raise Exception(
                f"This script is for Docker/Linux only, detected: {system}"
            )

        del base_dir
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg must be installed by the operating system package manager"
            )
        logger.info(f"[green]Using system ffmpeg: {ffmpeg}[/green]")
        return ffmpeg


def main() -> None:
    """CLI entry point for Docker build-time ffmpeg validation."""
    print(FfmpegBinaryManager.download_ffmpeg_for_docker())


if __name__ == "__main__":
    main()
