"""Domain outcomes for externally hosted release images."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ImageUploadFailureKind(StrEnum):
    HOST_UNAVAILABLE = "host_unavailable"
    CREDENTIAL_REJECTED = "credential_rejected"
    PAYLOAD_REJECTED = "payload_rejected"
    RESPONSE_INVALID = "response_invalid"
    LOCAL_IMAGE_UNREADABLE = "local_image_unreadable"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, slots=True)
class HostedImage:
    thumbnail_url: str
    raw_url: str
    page_url: str
    local_path: str | None = None


@dataclass(frozen=True, slots=True)
class ImageUploadFailure:
    host: str
    kind: ImageUploadFailureKind
    reason: str
    retry_same_host: bool = False


@dataclass(frozen=True, slots=True)
class ImageUploadOutcome:
    image: HostedImage | None = None
    failure: ImageUploadFailure | None = None

    @property
    def succeeded(self) -> bool:
        return self.image is not None
