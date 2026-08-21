import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.polishtorrent import PolishTorrent


def _movie_meta(**kwargs):
    images = [
        {
            "raw_url": f"https://images.example/full-{index}.png",
            "img_url": f"https://images.example/thumb-{index}.png",
        }
        for index in range(3)
    ]
    values = {
        "category": "MOVIE",
        "filelist": ["Example.Movie.2024.mkv"],
        "image_list": images,
        "mediainfo": {"media": {"track": []}},
        "name": "Example Movie 2024 1080p WEB-DL H264",
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    values.update(kwargs)
    return Meta(**values)


def test_polishtorrent_accepts_png_and_tiff_screenshot_links():
    image = {
        "raw_url": "https://images.example/full.png",
        "web_url": "https://images.example/view",
        "img_url": "https://images.example/thumb.tiff",
    }

    assert PolishTorrent._is_allowed_screenshot_image(image) is True
    assert PolishTorrent._has_valid_screenshot_thumb_and_full(image) is True


def test_polishtorrent_rejects_mixed_or_malformed_screenshot_links():
    mixed = {
        "raw_url": "https://images.example/full.png",
        "img_url": "https://images.example/thumb.jpg",
    }
    malformed = {
        "raw_url": "http://[",
        "img_url": "https://images.example/thumb.png",
    }

    assert PolishTorrent._is_allowed_screenshot_image(mixed) is False
    assert PolishTorrent._is_allowed_screenshot_image(malformed) is False


def test_polishtor_rejects_extension_screen_links_th_ea117a():
    image = {
        "raw_url": "https://images.example/full",
        "img_url": "https://images.example/thumb",
    }

    assert PolishTorrent._is_allowed_screenshot_image(image) is False
    assert (
        asyncio.run(
            PolishTorrent({"TRACKERS": {}}).get_additional_checks(
                _movie_meta(image_list=[image])
            )
        )
        is False
    )


def test_polishtorrent_rejects_malformed_filelist_and_screenshot_counts():
    tracker = PolishTorrent({"TRACKERS": {}})

    assert (
        asyncio.run(tracker.get_additional_checks(_movie_meta(filelist=1)))
        is False
    )
    assert (
        asyncio.run(tracker.get_additional_checks(_movie_meta(filelist="")))
        is False
    )
    assert (
        asyncio.run(tracker.get_additional_checks(_movie_meta(image_list=1)))
        is False
    )
    assert (
        asyncio.run(tracker.get_additional_checks(_movie_meta(screens=None)))
        is False
    )
    assert (
        asyncio.run(tracker.get_additional_checks(_movie_meta(screens="2")))
        is False
    )
    assert (
        asyncio.run(
            tracker.get_additional_checks(_movie_meta(screens=float("inf")))
        )
        is False
    )
    assert (
        asyncio.run(tracker.get_additional_checks(_movie_meta(image_list=[])))
        is False
    )


@pytest.mark.parametrize(
    "archive",
    ["release.r10", "release.r99", "release.7z.001", "release.rar.002"],
)
def test_polishtorrent_rejects_extended_multipart_archives(
    archive: str,
) -> None:
    assert (
        asyncio.run(
            PolishTorrent({"TRACKERS": {}}).get_additional_checks(
                _movie_meta(filelist=["movie.mkv", archive])
            )
        )
        is False
    )


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url.png",
        "/relative/full.png",
        "ftp://images.example/full.png",
        "https:///full.png",
        "not a URL",
    ],
)
def test_polishtorrent_rejects_non_http_absolute_screenshot_links(url):
    assert (
        PolishTorrent._is_allowed_screenshot_image({"raw_url": url}) is False
    )


def test_polishtor_tracker_detect_ignores_url_paths_ace353():
    assert (
        PolishTorrent._contains_other_tracker_mention(
            "https://images.example/kat/rutracker/screen.png"
        )
        is False
    )
    assert (
        PolishTorrent._contains_other_tracker_mention(
            "//rutracker.net./release"
        )
        is True
    )
    assert (
        PolishTorrent._contains_other_tracker_mention(
            "https://rutracker.net?mirror=1"
        )
        is True
    )
    assert (
        PolishTorrent._contains_other_tracker_mention(
            "https://(rutracker.net)/release"
        )
        is True
    )
    assert (
        PolishTorrent._contains_other_tracker_mention(
            "https://limetorrents.cc/release"
        )
        is True
    )
    assert PolishTorrent._contains_other_tracker_mention("https://[") is False
    assert (
        PolishTorrent._contains_other_tracker_mention("mirrored from YIFY")
        is True
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Example.Movie.2024.1080p.WEB-DL.H264",
            "Example Movie 2024 1080p WEB-DL H264",
        ),
        (
            "Example Movie (2024) 1080p WEB-DL H264",
            "Example Movie 2024 1080p WEB-DL H264",
        ),
        (
            "Example Movie 2024 1080p WEB-DL DD+ 5.1 H.264",
            "Example Movie 2024 1080p WEB-DL DD+ 5 1 H 264",
        ),
    ],
)
def test_polishtorrent_normalizes_tracker_name_before_validation(
    name: str, expected: str
) -> None:
    tracker = PolishTorrent({"TRACKERS": {}})
    meta = _movie_meta(name=name)

    assert asyncio.run(tracker.get_name(meta)) == {"name": expected}
    assert asyncio.run(tracker.get_additional_checks(meta)) is True
    assert meta.name == name


def test_polishtor_not_infer_boxset_video_count_comp_d5065a():
    files = ["Movie.One.2024.mkv", "Movie.Two.2024.mkv"]
    assert PolishTorrent._is_boxset_style("A Complete Unknown", files) is False


def test_polishtorrent_tv_pack_status_and_disc_folder_handling():
    tracker = PolishTorrent({"TRACKERS": {}})
    unknown_pack = _movie_meta(
        category="TV",
        tv_pack=True,
        imdb_info={},
        name="Example Show S01 1080p WEB-DL H264",
    )
    assert PolishTorrent._is_tv_pack_ended(unknown_pack) is None
    assert asyncio.run(tracker.get_additional_checks(unknown_pack)) is False
    assert (
        asyncio.run(
            tracker.get_additional_checks(
                _movie_meta(
                    is_disc="BDMV",
                    keep_folder=True,
                    mediainfo=None,
                    filelist=["BDMV"],
                )
            )
        )
        is True
    )


def test_polishtorrent_image_url_guard_branches():
    assert PolishTorrent._extract_image_url("bad", "raw_url") == ""
    assert PolishTorrent._extract_image_url({"raw_url": 123}, "raw_url") == ""
    assert not PolishTorrent._is_forbidden_tracker_url("not-a-url")


def test_polishtorrent_rejects_tracker_reference_in_description():
    meta = _movie_meta(
        description="Mirrored from https://rutracker.net/release"
    )
    assert not asyncio.run(
        PolishTorrent({"TRACKERS": {}}).get_additional_checks(meta)
    )


def test_polishtorrent_rejects_unsupported_screenshot_metadata():
    images = [
        {
            "raw_url": "https://images.example/full-1.png",
            "img_url": "https://images.example/thumb-1.png",
        },
        {
            "raw_url": "https://images.example/full-2.png",
            "img_url": "https://images.example/thumb-2.png",
        },
        {
            "raw_url": "https://images.example/full-3.jpg",
            "img_url": "https://images.example/thumb-3.jpg",
        },
    ]
    assert not asyncio.run(
        PolishTorrent({"TRACKERS": {}}).get_additional_checks(
            _movie_meta(image_list=images)
        )
    )


def test_polishtorrent_rejects_screenshot_without_thumb_and_full_links():
    images = [
        {
            "raw_url": "https://images.example/full-1.png",
            "img_url": "https://images.example/thumb-1.png",
        },
        {
            "raw_url": "https://images.example/full-2.png",
            "img_url": "https://images.example/thumb-2.png",
        },
        {"raw_url": "https://images.example/full-3.png"},
    ]
    assert not asyncio.run(
        PolishTorrent({"TRACKERS": {}}).get_additional_checks(
            _movie_meta(image_list=images)
        )
    )


def test_polishtorrent_requires_mediainfo_for_non_disc_video():
    assert not asyncio.run(
        PolishTorrent({"TRACKERS": {}}).get_additional_checks(
            _movie_meta(mediainfo=None)
        )
    )


def test_polishtorrent_rejects_movie_boxset():
    meta = _movie_meta(name="Example Movie Collection 2024 1080p WEB-DL H264")
    assert not asyncio.run(
        PolishTorrent({"TRACKERS": {}}).get_additional_checks(meta)
    )


def test_polishtorrent_rejects_ongoing_tv_pack(monkeypatch):
    tracker = PolishTorrent({"TRACKERS": {}})
    monkeypatch.setattr(
        PolishTorrent, "_is_tv_pack_ended", staticmethod(lambda _meta: False)
    )
    meta = _movie_meta(
        category="TV", tv_pack=True, name="Example Show S01 1080p WEB-DL H264"
    )
    assert not asyncio.run(tracker.get_additional_checks(meta))


def test_polishtorrent_rejects_single_video_inside_folder():
    meta = _movie_meta(keep_folder=True, filelist=["Example.Movie.2024.mkv"])
    assert not asyncio.run(
        PolishTorrent({"TRACKERS": {}}).get_additional_checks(meta)
    )


def test_polishtorrent_raw_title_guard_branches() -> None:
    tracker = PolishTorrent({"TRACKERS": {}})
    assert tracker._has_banned_title_chars("Movie.2024") is True
    assert tracker._title_policy("MOVIE", "Movie.2024") is False


def test_polishtorrent_ended_pack_and_multi_file_folder_are_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = PolishTorrent({"TRACKERS": {}})
    monkeypatch.setattr(
        PolishTorrent,
        "_is_tv_pack_ended",
        staticmethod(lambda _meta: True),
    )
    assert tracker._tv_pack_policy(
        _movie_meta(category="TV", tv_pack=True), "TV"
    )
    assert tracker._folder_policy(
        _movie_meta(keep_folder=True),
        "MOVIE",
        ["Movie.Part1.mkv", "Movie.Part2.mkv"],
    )


def test_polishtorrent_localizes_polish_title_before_normalization() -> None:
    tracker = PolishTorrent({"TRACKERS": {}})
    meta = _movie_meta(
        name="Original.Title.2024.1080p.WEB-DL.H264",
        title="Original Title",
        aka="AKA Localized Title",
        original_language="pl",
        imdb_info={"aka": "Polski Tytul"},
    )

    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Polski Tytul 2024 1080p WEB-DL H264"
    }
