from __future__ import annotations

from src.meta import Meta
from src.trackers.UNIT3D.capybarabr import CapybaraBR


def _tracker() -> CapybaraBR:
    return CapybaraBR({"TRACKERS": {"CAPYBARABR": {}}})


def _video_meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "MOVIE",
        "type": "WEBDL",
        "resolution": "1080p",
        "screens": 3,
        "name": "Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GROUP",
        "filelist": ["Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GROUP.mkv"],
    }
    defaults.update(values)
    return Meta(**defaults)


def test_compressed_media_is_reported() -> None:
    for name in ("release.rar", "release.zip", "release.7z", "release.r00", "release.001"):
        assert CapybaraBR._payload_skip_reason(
            _video_meta(filelist=["Movie.2024.mkv", name])
        ) == (
            "compressed media archives are not allowed for movies or shows; "
            "extract the release first"
        )
    assert CapybaraBR._payload_skip_reason(
        _video_meta(category="BOOK", filelist=["book.epub", "book.cbz"])
    ) == ""


def test_sidecar_matches_video_stem_and_language_suffix() -> None:
    assert (
        CapybaraBR._payload_skip_reason(
            _video_meta(
                filelist=["Show.S01E01.1080p.mkv"],
                subtitle_files=["Show.S01E01.1080p.pt-BR.srt"],
            )
        )
        == ""
    )
    assert (
        CapybaraBR._payload_skip_reason(
            _video_meta(
                filelist=["Show.S01E01.1080p.mkv"],
                subtitle_files=["Show.S01E01.1080p.eng.ass"],
            )
        )
        == ""
    )


def test_orphan_sidecar_is_reported() -> None:
    meta = _video_meta(
        filelist=["Show.S01E01.1080p.mkv"],
        subtitle_files=["Show.S01E02.1080p.srt"],
    )
    assert CapybaraBR._payload_skip_reason(meta) == (
        "sidecar subtitle 'Show.S01E02.1080p.srt' has no matching video file in the payload"
    )


def test_sidecar_without_videos_fails_open() -> None:
    assert (
        CapybaraBR._payload_skip_reason(
            _video_meta(filelist=["Show.S01E01.srt"], subtitle_files=[])
        )
        == ""
    )


def test_screenshot_minimum_for_movies_and_shows() -> None:
    tracker = _tracker()
    assert tracker._screenshot_allowed(_video_meta(screens=3)) is True
    assert tracker._screenshot_allowed(_video_meta(screens=2)) is False
    assert tracker._screenshot_allowed(_video_meta(category="BOOK", screens=0)) is True
    assert tracker._screenshot_allowed(_video_meta(screens=None)) is True
