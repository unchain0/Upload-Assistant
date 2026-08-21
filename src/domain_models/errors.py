"""Semantic domain errors shared by services and delivery."""

from __future__ import annotations


class UploadAssistantError(Exception):
    """Base class for expected domain failures."""


class ConfigurationError(UploadAssistantError):
    """The application configuration cannot be loaded or interpreted."""


class ConfigurationNotFoundError(ConfigurationError):
    """No usable configuration source exists."""


class ConfigurationSyntaxError(ConfigurationError):
    """A configuration source is not a literal Python configuration mapping."""


class TmdbCredentialMissingError(ConfigurationError):
    """No TMDb application credential is configured."""


class TmdbCredentialRejectedError(UploadAssistantError):
    """TMDb rejected the configured application credential."""


class MediaInfoError(UploadAssistantError, RuntimeError):
    """MediaInfo execution failure with concise text and diagnostic details."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str],
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message)

    @property
    def debug_details(self) -> str:
        return f"Command: {self.command!r}\nstdout:\n{self.stdout.strip() or '(empty)'}\nstderr:\n{self.stderr.strip() or '(empty)'}"


class ScreenshotCaptureError(UploadAssistantError):
    """Screenshot capture failed after infrastructure errors were translated."""


class ImageHostingUnavailableError(UploadAssistantError):
    """No eligible image host accepted the upload."""


class TrackerImageRequirementError(UploadAssistantError):
    """A tracker cannot be used because its required images are unavailable."""


class OperationAbortedError(UploadAssistantError):
    """The user or an explicit policy cancelled the current operation."""


class NoWorkAvailableError(UploadAssistantError):
    """The requested queue or selection contains no work to process."""
