"""Typed identifiers used across Upload Assistant's domain boundary."""

from typing import NewType

ReleaseID = NewType("ReleaseID", str)
TrackerName = NewType("TrackerName", str)
TmdbID = NewType("TmdbID", int)
ImdbID = NewType("ImdbID", int)
