"""Pure business vocabulary for Upload Assistant."""

from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigurationSource,
    ConfigurationSourceKind,
)
from src.domain_models.external_api import TmdbCredential, TmdbCredentialMode
from src.domain_models.ids import ImdbID, ReleaseID, TmdbID, TrackerName
from src.domain_models.image_upload import (
    HostedImage,
    ImageUploadFailure,
    ImageUploadFailureKind,
    ImageUploadOutcome,
)

__all__ = [
    "ApplicationConfiguration",
    "ConfigurationSource",
    "ConfigurationSourceKind",
    "HostedImage",
    "ImageUploadFailure",
    "ImageUploadFailureKind",
    "ImageUploadOutcome",
    "ImdbID",
    "ReleaseID",
    "TmdbCredential",
    "TmdbCredentialMode",
    "TmdbID",
    "TrackerName",
]
