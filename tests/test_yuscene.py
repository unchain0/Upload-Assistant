import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.yuscene import YUSCENE


def _tracker() -> YUSCENE:
    return YUSCENE({"DEFAULT": {}, "TRACKERS": {"YUSCENE": {}}})


def _movie_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "MOVIE",
        "mediainfo": {"media": {"track": []}},
        "filelist": ["Movie.2024.mkv"],
        "name": "Example Movie 2024",
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def _tv_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "TV",
        "mediainfo": {"media": {"track": []}},
        "filelist": ["Show.S01E01.mkv"],
        "name": "Example Series",
        "season": 1,
        "tv_pack": False,
        "screens": 3,
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def _book_meta(**kwargs: Any) -> Meta:
    base: dict[str, Any] = {
        "category": "BOOK",
        "author": "Kenji Miyazawa",
        "title": "Complete Collection of Children's Stories",
        "book_overview": "This complete audiobook collection presents the celebrated children's stories written by Kenji Miyazawa.",
        "filelist": ["audiobook.m4b"],
        "unattended": True,
        "unattended_confirm": False,
    }
    base.update(kwargs)
    return Meta(**base)


def test_yuscene_blocks_adult_keywords_when_unattended():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(keywords=["Porn"], unattended=True, unattended_confirm=False))) is False


def test_yuscene_blocks_string_adult_keyword_when_unattended():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(keywords="Porn"))) is False


def test_yuscene_blocks_delimited_adult_keyword_when_unattended():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(keywords="Porn, Action"))) is False


def test_yuscene_rejects_malformed_filelist_and_screenshot_counts():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=1))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=""))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=None))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens="2"))) is False
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(screens=float("inf")))) is False


def test_yuscene_blocks_adult_media_flag():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(adult_media=True, unattended=True, unattended_confirm=False))) is False


def test_yuscene_accepts_non_adult_movie_in_unattended_mode():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta())) is True


def test_yuscene_rejects_book_without_english_title_and_description():
    meta = _book_meta(
        author="宮沢 賢治",
        title="宮沢賢治童話全集",
        book_overview="※本タイトルは30時間を超えるため、宮沢賢治の童話を収録しています。",
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_yuscene_translates_non_english_book_metadata_before_upload():
    meta = _book_meta(
        author="宮沢 賢治",
        title="宮沢賢治童話全集",
        name="宮沢 賢治 - 宮沢賢治童話全集 2016 JAPANESE AUDIOBOOK",
        book_overview="※本タイトルは30時間を超えるため、宮沢賢治の童話を収録しています。",
    )
    tracker = YUSCENE({"DEFAULT": {"google_translate_api_key": "translation-key"}, "TRACKERS": {"YUSCENE": {}}})
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": {
            "translations": [
                {"translatedText": "Kenji Miyazawa"},
                {"translatedText": "Complete Collection of Children's Stories"},
                {"translatedText": ("This complete audiobook collection presents the celebrated children's stories written by Kenji Miyazawa.")},
            ]
        }
    }

    with patch("src.integrations.trackers.UNIT3D.yuscene.httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as translate:
        assert asyncio.run(tracker.get_additional_checks(meta)) is True

    translate.assert_awaited_once()
    assert meta.author == "Kenji Miyazawa"
    assert meta.title == "Complete Collection of Children's Stories"
    assert meta.book_overview.startswith("This complete audiobook collection")
    assert meta.name == "Kenji Miyazawa - Complete Collection of Children's Stories 2016 JAPANESE AUDIOBOOK"


def test_yuscene_accepts_verified_english_book_metadata_with_original_text_optional():
    meta = _book_meta(
        author="Kenji Miyazawa 宮沢 賢治",
        title="Complete Collection of Children's Stories 宮沢賢治童話全集",
        book_overview=("This complete audiobook collection presents the celebrated children's stories written by Kenji Miyazawa. Original Japanese title: 宮沢賢治童話全集."),
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True


def test_yuscene_blocks_archive_files_for_non_games():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.rar"],
                )
            )
        )
        is False
    )


def test_yuscene_blocks_extra_files_in_movie_uploads():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    filelist=["Example.Movie.2024.mkv", "Example.Movie.2024.nfo"],
                )
            )
        )
        is False
    )


def test_yuscene_requires_mediainfo_for_movie():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(mediainfo={}))) is False


def test_yuscene_blocks_tv_pack_when_series_still_ongoing():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _tv_meta(
                    tv_pack=True,
                    imdb_info={"status": "Returning Series"},
                )
            )
        )
        is False
    )


def test_yuscene_does_not_classify_in_development_series_as_ended():
    assert asyncio.run(_tracker().get_additional_checks(_tv_meta(tv_pack=True, imdb_info={"status": "In Development"}))) is False


def test_yuscene_blocks_title_chars_for_movie():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example.Movie 2024"))) is False


def test_yuscene_allows_movies_without_forbidden_title_chars():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024"))) is True


def test_yuscene_blocks_other_tracker_mentions():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024 yify"))) is False


def test_yuscene_does_not_match_ambiguous_tracker_alias_inside_names():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Katniss Everdeen 2024"))) is True


def test_yuscene_allows_urls_in_description_when_tracker_reference_check_targets_title_only():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    description="Screens: https://i.imgur.com/example1.jpg\nhttps://www.imdb.com/title/tt1234567/",
                )
            )
        )
        is True
    )


def test_yuscene_ignores_tracker_tokens_in_legitimate_url_paths():
    assert YUSCENE._contains_other_tracker_mention("https://images.example/kat/rutracker/screen.png") == ""


def test_yuscene_tracker_detection_handles_malformed_urls():
    assert YUSCENE._contains_other_tracker_mention("https://[") == ""


def test_yuscene_detects_protocol_relative_tracker_domain_with_trailing_dot():
    assert YUSCENE._contains_other_tracker_mention("//rutracker.net./release") == "rutracker.net"


def test_yuscene_blocks_disallowed_tracker_domains_in_title():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie 2024 from rutracker.net"))) is False


def test_yuscene_blocks_low_screenshot_count():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    screens=2,
                )
            )
        )
        is False
    )


def test_yuscene_allows_movie_with_three_screenshots():
    assert (
        asyncio.run(
            _tracker().get_additional_checks(
                _movie_meta(
                    screens=3,
                    filelist=["Example.Movie.2024.mkv"],
                )
            )
        )
        is True
    )


def test_yuscene_rejects_game_package_without_valid_type_mapping():
    meta = Meta(category="GAME", type="GAME", filelist=["dungeon_antiqua_2_enUS_20260717_.pkg"], unattended=True)

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False
    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": "0"}


def test_yuscene_maps_rar_game_release_to_archive_type():
    meta = Meta(category="GAME", type="GAME", filelist=["release.rar", "release.r00"], unattended=True)

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True
    assert asyncio.run(_tracker().get_type_id(meta)) == {"type_id": "22"}


def test_yuscene_attended_confirmation_uses_prompt():
    tracker = _tracker()
    tracker.common.prompt_user_for_confirmation = AsyncMock(return_value=True)
    assert asyncio.run(tracker._confirm_or_skip("Confirm", Meta(unattended=False))) is True
    tracker.common.prompt_user_for_confirmation.assert_awaited_once()


def test_yuscene_detects_sample_video_extra_and_bracket_title():
    assert YUSCENE._contains_video_extras(["sample.mkv"]) == "sample.mkv"
    assert YUSCENE._has_banned_title_chars("Movie [2024]") is True
    assert YUSCENE._forbidden_tracker_host("not-a-url") == ""


def test_yuscene_translation_requires_complete_source_values():
    tracker = YUSCENE({"DEFAULT": {"google_translate_api_key": "translation-key"}, "TRACKERS": {"YUSCENE": {}}})
    meta = _book_meta(title="")
    assert asyncio.run(tracker._translate_book_metadata_to_english(meta)) is False


def test_yuscene_translation_http_error_is_nonfatal():
    import httpx

    tracker = YUSCENE({"DEFAULT": {"google_translate_api_key": "translation-key"}, "TRACKERS": {"YUSCENE": {}}})
    meta = _book_meta(author="宮沢 賢治", title="宮沢賢治童話全集", book_overview="日本語の説明です。")
    request_error = httpx.RequestError("offline", request=httpx.Request("POST", "https://translation.googleapis.com"))
    with patch("src.integrations.trackers.UNIT3D.yuscene.httpx.AsyncClient.post", new=AsyncMock(side_effect=request_error)):
        assert asyncio.run(tracker._translate_book_metadata_to_english(meta)) is False


def test_yuscene_translation_invalid_json_is_nonfatal():
    tracker = YUSCENE({"DEFAULT": {"google_translate_api_key": "translation-key"}, "TRACKERS": {"YUSCENE": {}}})
    meta = _book_meta(author="宮沢 賢治", title="宮沢賢治童話全集", book_overview="日本語の説明です。")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("bad json")
    with patch("src.integrations.trackers.UNIT3D.yuscene.httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        assert asyncio.run(tracker._translate_book_metadata_to_english(meta)) is False


def test_yuscene_translation_rejects_incomplete_results():
    tracker = YUSCENE({"DEFAULT": {"google_translate_api_key": "translation-key"}, "TRACKERS": {"YUSCENE": {}}})
    meta = _book_meta(author="宮沢 賢治", title="宮沢賢治童話全集", book_overview="日本語の説明です。")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"data": {"translations": [{"translatedText": "Kenji Miyazawa"}]}}
    with patch("src.integrations.trackers.UNIT3D.yuscene.httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        assert asyncio.run(tracker._translate_book_metadata_to_english(meta)) is False


def test_yuscene_translation_revalidates_translated_metadata():
    tracker = YUSCENE({"DEFAULT": {"google_translate_api_key": "translation-key"}, "TRACKERS": {"YUSCENE": {}}})
    meta = _book_meta(author="宮沢 賢治", title="宮沢賢治童話全集", book_overview="日本語の説明です。")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": {
            "translations": [
                {"translatedText": "Kenji Miyazawa"},
                {"translatedText": "Stories"},
                {"translatedText": "Short description"},
            ]
        }
    }
    tracker._has_english_book_metadata = Mock(return_value=False)
    with patch("src.integrations.trackers.UNIT3D.yuscene.httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
        assert asyncio.run(tracker._translate_book_metadata_to_english(meta)) is False


def test_yuscene_accepts_non_collection_keywords_payload():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(keywords=123))) is True


def test_yuscene_rejects_single_file_folder_and_unknown_tv_pack():
    tracker = _tracker()
    assert asyncio.run(tracker.get_additional_checks(_movie_meta(keep_folder=True))) is False
    assert asyncio.run(tracker.get_additional_checks(_tv_meta(tv_pack=True, imdb_info={}))) is False


def test_yuscene_rejects_movie_collection_name():
    assert asyncio.run(_tracker().get_additional_checks(_movie_meta(name="Example Movie Collection 2024"))) is False


def test_yuscene_unknown_book_type_defaults_to_pdf():
    assert asyncio.run(_tracker().get_type_id(Meta(category="BOOK", type="DOCX"))) == {"type_id": "21"}
