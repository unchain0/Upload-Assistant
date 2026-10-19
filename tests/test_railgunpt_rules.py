import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP
from src.integrations.trackers.NEXUSPHP.railgunpt import RailgunPT


def _tracker() -> RailgunPT:
    return RailgunPT(
        {"DEFAULT": {"tmdb_api": "test"}, "TRACKERS": {"RAILGUNPT": {}}}
    )


def _movie_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "MOVIE",
        "filelist": ["Example.Movie.2024.1080p.BluRay.x264-GRP.mkv"],
        "name": "Example Movie 2024 1080p BluRay DD 5.1 x264-GRP",
        "resolution": "1080p",
        "source": "BluRay",
        "source_size": 1024**3,
        "type": "ENCODE",
        "video_codec": "AVC",
        "video_encode": "x264",
        "year": 2024,
    }
    values.update(kwargs)
    return Meta(**values)


def _tv_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "TV",
        "filelist": ["Example.Show.S01E01.1080p.HDTV.x264-GRP.mkv"],
        "name": "Example Show S01E01 1080p HDTV DD 5.1 x264-GRP",
        "resolution": "1080p",
        "source": "HDTV",
        "source_size": 1024**3,
        "type": "HDTV",
        "video_codec": "AVC",
        "video_encode": "x264",
    }
    values.update(kwargs)
    return Meta(**values)


def _music_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "MUSIC",
        "filelist": [
            "Artist - Album - 01.flac",
            "Artist - Album - 02.flac",
            "Album.cue",
        ],
        "name": "Artist - Album 2024 FLAC",
        "format": "FLAC",
        "source": "CD",
        "source_size": 1024**3,
        "music_release": {
            "fields": {"release_type": {"value": "Album"}},
            "auxiliary": {"cues": ["Album.cue"]},
            "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        },
    }
    values.update(kwargs)
    return Meta(**values)


def _game_meta(**kwargs: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "GAME",
        "filelist": ["Command.And.Conquer.iso"],
        "name": "Command And Conquer Red Alert 3 Uprising",
        "source_size": 1024**3,
        "type": "GAME",
        "platform": "PC",
        "game_version": "1.0",
    }
    values.update(kwargs)
    return Meta(**values)


def _check(meta: Meta) -> bool:
    return asyncio.run(_tracker().get_additional_checks(meta))


def _touch_files(paths: list[Path]) -> None:
    for path in paths:
        path.touch()


def _assert_invalid_cue_cases(
    root: Path,
    release: dict[str, Any],
    track_one: Path,
    track_two: Path,
) -> None:
    invalid_cue_cases = {
        "MissingIndex.cue": 'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 02 00:00:00\n',
        "InvalidTime.cue": 'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:99:99\n',
        "InvalidFrame.cue": 'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:75\n',
        "ZeroTrack.cue": 'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 00 AUDIO\nINDEX 01 00:00:00\n',
        "UnknownDirective.cue": 'FILE "CD1/Artist - Album - 01.flac" WAVE\nUNKNOWN "unexpected"\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n',
    }
    for cue_name, cue_content in invalid_cue_cases.items():
        (root / cue_name).write_text(cue_content)
        release["auxiliary"] = {"cues": [cue_name]}
        assert (
            _check(
                _music_meta(
                    path=str(root),
                    filelist=[str(track_one), str(track_two)],
                    music_release=release,
                )
            )
            is False
        )


def test_railgunpt_accepts_compliant_movie_and_tv_uploads():
    assert _check(_movie_meta()) is True
    assert _check(_tv_meta()) is True


def test_railgunpt_rejects_unsupported_and_sensitive_content():
    assert _check(_movie_meta(category="BOOK")) is False
    assert _check(_movie_meta(adult_media=True)) is False
    assert _check(_movie_meta(keywords=["Political"])) is False
    assert _check(_movie_meta(keywords="Political")) is False


def test_railgunpt_normalizes_scalar_category_metadata():
    assert _tracker().get_category(_movie_meta(genres="Documentary")) == 404
    assert _tracker().get_category(_movie_meta(keywords="Animation")) == 405


def test_railgunpt_enforces_video_minimum_size():
    assert _check(_movie_meta(source_size=100 * 1024 * 1024)) is True
    assert _check(_movie_meta(source_size=100 * 1024 * 1024 - 1)) is False
    assert _check(_movie_meta(source_size=0)) is False
    assert _check(_movie_meta(source_size=-1)) is False


@pytest.mark.parametrize(
    "marker", ["CAM", "TC", "TS", "SCR", "DVDSCR", "R5", "HalfCD"]
)
def test_railgunpt_rejects_low_quality_source_markers(marker: str):
    assert (
        _check(
            _movie_meta(
                name=f"Example Movie 2024 {marker} 1080p BluRay x264-GRP"
            )
        )
        is False
    )


def test_railgunpt_enforces_sd_source_rules_and_rejects_sd_upscales():
    assert (
        _check(
            _movie_meta(
                name="Example Movie 2024 576p BluRay x264-GRP",
                resolution="576p",
            )
        )
        is True
    )
    assert (
        _check(
            _movie_meta(
                name="Example Movie 2024 576p WEB-DL x264-GRP",
                resolution="576p",
                source="WEB-DL",
                type="WEBDL",
            )
        )
        is False
    )
    assert (
        _check(
            _movie_meta(
                name="Example Movie 2024 UPSCALE 576p BluRay x264-GRP",
                resolution="576p",
            )
        )
        is False
    )
    assert (
        _check(
            _movie_meta(
                name="Example Movie 2024 360p BluRay x264-GRP",
                resolution="360p",
            )
        )
        is False
    )


def test_railgunpt_rejects_archives_spam_realvideo_and_individual_samples():
    assert _check(_movie_meta(filelist=["release.rar"])) is False
    assert (
        _check(_movie_meta(filelist=["downloaded from tracker.url"])) is False
    )
    assert (
        _check(_movie_meta(filelist=["movie.rmvb"], video_codec="RealVideo"))
        is False
    )
    assert (
        _check(_movie_meta(video_codec="RV40", video_encode="x264")) is False
    )
    assert (
        _check(_movie_meta(video_codec="AVC", video_encode="RealVideo 10"))
        is False
    )
    assert _check(_movie_meta(filelist=["sample.mkv"])) is False
    assert _check(_movie_meta(filelist=["release.nfo"])) is False


def test_railgunpt_allows_permitted_archived_attachments_and_main_samples():
    assert _check(_movie_meta(filelist=["movie.mkv", "subtitles.rar"])) is True
    assert _check(_movie_meta(filelist=["movie.mkv", "sample.mkv"])) is True


@pytest.mark.parametrize(
    "name",
    [
        "Example Movie 1080p BluRay x264-GRP",
        "Example Movie 2024 BluRay x264-GRP",
        "Example Movie 2024 1080p x264-GRP",
        "Example Movie 2024 1080p BluRay-GRP",
    ],
)
def test_railgunpt_requires_descriptive_movie_title(name: str):
    assert _check(_movie_meta(name=name)) is False


def test_railgunpt_rejects_video_tokens_embedded_in_words():
    assert (
        _check(
            _movie_meta(
                name="Example Movie 2024 1080p NotBluRayish x264codec-GRP"
            )
        )
        is False
    )


def test_railgunpt_requires_tv_season_episode_or_pack_token():
    assert _check(_tv_meta(name="Example Show 1080p HDTV x264-GRP")) is False
    assert (
        _check(
            _tv_meta(name="Example Show S01 1080p HDTV x264-GRP", tv_pack=True)
        )
        is True
    )


def test_railgunpt_enforces_pack_consistency():
    files = [
        "Show.S01E01.1080p.HDTV.x264-GRP.mkv",
        "Show.S01E02.720p.HDTV.x264-GRP.mkv",
    ]
    assert (
        _check(
            _tv_meta(
                name="Example Show S01 1080p HDTV x264-GRP",
                tv_pack=True,
                filelist=files,
            )
        )
        is False
    )

    files = [
        "Show.S01E01.2160p.UHDTV.x265-GRP.mkv",
        "Show.S01E02.2160p.HDTV.x265-GRP.mkv",
    ]
    assert (
        _check(
            _tv_meta(
                name="Example Show S01 2160p UHDTV x265-GRP",
                tv_pack=True,
                filelist=files,
            )
        )
        is False
    )

    files = [
        "Show.S01E01.1080p.HDTV.x264-GRP.mkv",
        "Show.S01E02.1080p.HDTV-GRP.mkv",
    ]
    assert (
        _check(
            _tv_meta(
                name="Example Show S01 1080p HDTV x264-GRP",
                tv_pack=True,
                filelist=files,
            )
        )
        is False
    )

    files = [
        "Show.S01E01.1080p.HDTV.x264-GRP.mkv",
        "Show.S01E02.1080p.WEB-DL.x265-GRP.mkv",
    ]
    assert (
        _check(
            _tv_meta(
                name="Example Show S01 1080p HDTV x264-GRP",
                tv_pack=True,
                filelist=files,
            )
        )
        is False
    )


def test_railgunpt_requires_official_boxset_marker_for_multi_movie_uploads():
    files = [
        "Movie.One.2020.1080p.BluRay.x264.mkv",
        "Movie.Two.2022.1080p.BluRay.x264.mkv",
    ]
    assert _check(_movie_meta(filelist=files)) is False
    assert (
        _check(
            _movie_meta(
                name="Example Collection 2024 1080p BluRay x264-GRP",
                filelist=files,
            )
        )
        is True
    )


def test_railgunpt_allows_multi_file_disc_layout_without_collection_marker():
    files = ["BDMV/STREAM/00001.m2ts", "BDMV/STREAM/00002.m2ts"]
    assert _check(_movie_meta(is_disc="BDMV", filelist=files)) is True
    assert _check(_movie_meta(is_disc="unknown", filelist=files)) is False


def test_railgunpt_rejects_invalid_filelist_metadata():
    assert _check(_movie_meta(filelist=1)) is False


def test_railgunpt_rejects_lossy_audio_below_51_channels():
    for extension, codec in (("mp3", "MP3"), ("ac3", "AC3"), ("dts", "DTS")):
        files = ["movie.mkv", f"track01.{extension}"]
        assert (
            _check(_movie_meta(filelist=files, audio=codec, channels="2.0"))
            is False
        )
        assert (
            _check(_movie_meta(filelist=files, audio=codec, channels="5.1"))
            is True
        )


def test_railgunpt_requires_cue_for_multitrack_audio(tmp_path):
    root = tmp_path / "album"
    root.mkdir()
    track_one = root / "track01.flac"
    track_two = root / "track02.flac"
    track_one.touch()
    track_two.touch()
    cue = root / "album.cue"
    cue.write_text(
        'FILE "track01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "track02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    movie = root / "movie.mkv"
    movie.touch()
    files = ["movie.mkv", "track01.flac", "track02.flac"]
    assert _check(_movie_meta(filelist=files, audio="FLAC")) is False
    assert (
        _check(
            _movie_meta(
                path=str(root),
                filelist=[
                    str(movie),
                    str(track_one),
                    str(track_two),
                    str(cue),
                ],
                audio="FLAC",
            )
        )
        is True
    )


def test_railgunpt_rejects_multipart_archives_and_mixed_attachment_packing():
    assert _check(_movie_meta(filelist=["movie.mkv", "release.r03"])) is False
    assert (
        _check(_movie_meta(filelist=["movie.mkv", "subtitles.r03"])) is False
    )
    assert (
        _check(_movie_meta(filelist=["movie.mkv", "subtitles.rar.001"]))
        is False
    )
    assert (
        _check(_movie_meta(filelist=["movie.mkv", "subtitles.part01.rar"]))
        is False
    )
    assert (
        _check(
            _movie_meta(filelist=["movie.mkv", "subtitles.rar", "cover.jpg"])
        )
        is False
    )
    assert (
        _check(
            _movie_meta(filelist=["movie.mkv", "subtitles.rar", "cover.rar"])
        )
        is True
    )


def test_railgunpt_allows_sd_sports_from_tv_or_dsr():
    sports = _tv_meta(
        name="Sports Event 2024.08.13 576p DSR x264-GRP",
        resolution="576p",
        source="DSR",
        genres=["Sports"],
    )
    assert _check(sports) is True
    assert (
        _check(
            _tv_meta(
                name=sports.name,
                resolution="576p",
                source="DSR",
                genres=["Drama"],
            )
        )
        is False
    )


def test_railgunpt_prioritizes_sports_category_over_documentary_tag():
    assert (
        _tracker().get_category(_tv_meta(genres=["Sports", "Documentary"]))
        == 407
    )


def test_railgunpt_requires_consistent_movie_collection_media():
    files = [
        "Movie.One.2020.1080p.BluRay.x264.mkv",
        "Movie.Two.2022.720p.BluRay.x264.mkv",
    ]
    assert (
        _check(
            _movie_meta(
                name="Example Collection 2024 1080p BluRay x264-GRP",
                filelist=files,
            )
        )
        is False
    )

    consistent = [
        "Movie.One.2020.1080p.BluRay.x264.mkv",
        "Movie.Two.2022.1080p.BluRay.x264.mkv",
    ]
    assert (
        _check(
            _movie_meta(
                name="Example Collection 2024 1080p BluRay x264-GRP",
                filelist=consistent,
            )
        )
        is True
    )


def test_railgunpt_supports_music_and_game_categories_with_known_mappings():
    assert RailgunPT.supported_categories == ("TV", "MOVIE", "MUSIC", "GAME")
    assert _tracker().get_category(_tv_meta(genres=["Sports"])) == 407
    assert (
        _tracker().get_category(_tv_meta(genres=[], keywords=["Sports"]))
        == 407
    )
    assert _tracker().get_category(_music_meta()) == 408
    assert _tracker().get_category(_game_meta()) == 412
    assert _tracker().get_category(_game_meta(software=True)) == 410
    assert _tracker().get_type(_music_meta()) == 8
    assert _tracker().get_audio_codec(_music_meta()) == 5


def test_railgunpt_skips_tmdb_localization_for_non_video_categories():
    tracker = _tracker()
    asyncio.run(tracker.load_localized_data(_music_meta()))
    assert tracker.tmdb_data == {}
    asyncio.run(tracker.load_localized_data(_game_meta()))
    assert tracker.tmdb_data == {}


def test_railgunpt_handles_non_video_nexusphp_payload_and_search(
    monkeypatch: pytest.MonkeyPatch,
):
    tracker = _tracker()
    tracker.announce_url = "https://tracker.example/announce"
    observed: dict[str, Any] = {}

    async def fake_search(
        _tracker: NEXUSPHP, meta: Meta
    ) -> list[dict[str, str]]:
        observed.update(
            {
                "season": meta.season,
                "episode": meta.episode,
                "tv_pack": meta.tv_pack,
            }
        )
        return []

    monkeypatch.setattr(NEXUSPHP, "search_existing", fake_search)
    game = _game_meta(season=0, episode="", tv_pack=False)
    assert asyncio.run(tracker.get_technical_info(game)) == {}
    assert asyncio.run(tracker.get_technical_info(_music_meta())) == {}
    assert asyncio.run(tracker.search_existing(game)) == []
    assert observed == {"season": "", "episode": "", "tv_pack": False}
    assert (game.season, game.episode, game.tv_pack) == (0, "", False)


def test_railgunpt_applies_music_size_and_pack_rules(tmp_path):
    def payload(
        name: str, extensions: tuple[str, ...], *, cue: bool = True
    ) -> tuple[str, list[str]]:
        root = tmp_path / name
        root.mkdir()
        tracks = []
        for index, extension in enumerate(extensions, 1):
            track = root / f"track{index:02d}.{extension}"
            track.touch()
            tracks.append(str(track))
        if cue:
            cue_path = root / "Album.cue"
            cue_path.write_text(
                "".join(
                    f'FILE "{Path(track).name}" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
                    for track in tracks
                )
            )
            tracks.append(str(cue_path))
        return str(root), tracks

    flac_root, flac_tracks = payload("flac", ("flac", "flac"))
    mixed_root, mixed_tracks = payload("mixed", ("flac", "mp3"))
    m4a_root, m4a_tracks = payload("m4a", ("m4a", "m4a"))
    ogg_root, ogg_tracks = payload("ogg", ("ogg", "ogg"))
    mp3_root, mp3_tracks = payload("mp3", ("mp3", "mp3"))
    no_cue_root, no_cue_tracks = payload("no-cue", ("flac", "flac"), cue=False)
    assert (
        _check(
            _music_meta(
                source_size=100 * 1024 * 1024 - 1,
                path=flac_root,
                filelist=flac_tracks,
                music_release={
                    "fields": {"release_type": {"value": "Single"}}
                },
            )
        )
        is True
    )
    assert _check(_music_meta(source_size=100 * 1024 * 1024 - 1)) is False
    assert (
        _check(
            _music_meta(
                path=flac_root,
                filelist=flac_tracks,
                music_release={
                    "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is True
    )
    assert (
        _check(
            _music_meta(
                path=mixed_root,
                channels="5.1",
                filelist=mixed_tracks,
                music_release={
                    "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                path=no_cue_root,
                filelist=no_cue_tracks,
                music_release={
                    "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                path=m4a_root,
                channels="5.1",
                filelist=m4a_tracks,
                music_release={
                    "tracks": [{"format": "AAC"}, {"format": "AAC"}],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is True
    )
    assert (
        _check(
            _music_meta(
                path=ogg_root,
                channels="5.1",
                filelist=ogg_tracks,
                music_release={
                    "tracks": [
                        {"format": "Ogg Vorbis"},
                        {"format": "Ogg Vorbis"},
                    ],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is True
    )
    assert (
        _check(
            _music_meta(
                path=mp3_root,
                filelist=mp3_tracks,
                format="MP3",
                channels="",
                music_release={
                    "tracks": [
                        {"format": "MP3", "channels": 2},
                        {"format": "MP3", "channels": 2},
                    ],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                path=mp3_root,
                filelist=mp3_tracks,
                format="MP3",
                channels="",
                music_release={
                    "tracks": [
                        {"format": "MP3", "channels": 5.1},
                        {"format": "MP3", "channels": 5.1},
                    ],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is True
    )
    assert (
        _check(
            _music_meta(
                path=no_cue_root,
                filelist=no_cue_tracks,
                music_release={
                    "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
                    "auxiliary": {"cues": ["Album.cue"]},
                },
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                music_release={
                    "tracks": [
                        {"format": "FLAC", "album": "Album One"},
                        {"format": "FLAC", "album": "Album Two"},
                    ],
                    "auxiliary": {"cues": ["Albums.cue"]},
                }
            )
        )
        is False
    )


def test_railgunpt_applies_original_game_image_and_software_exceptions():
    assert _check(_game_meta()) is True
    assert _check(_game_meta(filelist=["game.cue"])) is False
    assert (
        _check(_game_meta(name="Garry's Mod", filelist=["Garrys.Mod.iso"]))
        is True
    )
    assert _check(_game_meta(filelist=["game.rar"])) is False
    assert (
        _check(_game_meta(filelist=["game.iso"], name="Game Portable Repack"))
        is False
    )
    assert (
        _check(
            _game_meta(
                filelist=["game.iso"], name="Game HighlyCompressed Edition"
            )
        )
        is False
    )
    assert (
        _check(
            _game_meta(filelist=["game.iso"], name="Game Re-Packed Edition")
        )
        is False
    )
    assert (
        _check(_game_meta(filelist=["game.iso"], name="Game Repacked Edition"))
        is False
    )
    assert (
        _check(
            _game_meta(
                software=True,
                source_size=100 * 1024 * 1024 - 1,
                filelist=["tool.pkg"],
                name="HD Video Tool",
            )
        )
        is True
    )


def test_railgunpt_requires_cue_in_music_payload(tmp_path):
    root = tmp_path / "album"
    attacker_root = tmp_path / "attacker"
    root.mkdir()
    attacker_root.mkdir()
    (attacker_root / "Unrelated.cue").touch()
    nested = root / "CD1"
    nested.mkdir()
    track_one = nested / "Artist - Album - 01.flac"
    track_two = nested / "Artist - Album - 02.flac"
    track_one.touch()
    track_two.touch()
    (root / "Album.cue").write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "CD1/Artist - Album - 02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release = {
        "root": str(root),
        "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        "auxiliary": {"cues": ["Album.cue"]},
    }
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is True
    )

    format_root = tmp_path / "format-layout"
    format_dir = format_root / "FLAC"
    format_dir.mkdir(parents=True)
    format_one = format_dir / "01.flac"
    format_two = format_dir / "02.flac"
    format_one.touch()
    format_two.touch()
    (format_root / "Album.cue").write_text(
        'FILE "FLAC/01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "FLAC/02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    format_release = {
        "root": str(format_root),
        "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        "auxiliary": {"cues": ["Album.cue"]},
    }
    assert (
        _check(
            _music_meta(
                path=str(format_root),
                filelist=[str(format_one), str(format_two)],
                music_release=format_release,
            )
        )
        is True
    )

    multi_root = tmp_path / "multi-disc"
    disc_one = multi_root / "CD1"
    disc_two = multi_root / "CD2"
    disc_one.mkdir(parents=True)
    disc_two.mkdir()
    multi_tracks = [disc_one / "01.flac", disc_two / "02.flac"]
    _touch_files(multi_tracks)
    (disc_one / "Album.cue").write_text(
        'FILE "01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    (disc_two / "Album.cue").write_text(
        'FILE "02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    multi_release = {
        "root": str(multi_root),
        "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        "auxiliary": {"cues": ["CD1/Album.cue", "CD2/Album.cue"]},
    }
    assert (
        _check(
            _music_meta(
                path=str(multi_root),
                filelist=[*(str(track) for track in multi_tracks)],
                music_release=multi_release,
            )
        )
        is True
    )

    mixed_cue = root / "Mixed.cue"
    mixed_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "../Outside.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["Mixed.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )
    release["auxiliary"] = {"cues": ["Album.cue"]}

    consecutive_file_cue = root / "ConsecutiveFile.cue"
    consecutive_file_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nFILE "CD1/Artist - Album - 02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["ConsecutiveFile.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )
    release["auxiliary"] = {"cues": ["Album.cue"]}

    release["root"] = "/"
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is True
    )

    release["root"] = str(attacker_root)
    release["auxiliary"] = {"cues": ["Unrelated.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    parent_root = tmp_path
    (parent_root / "Sibling.cue").write_text(
        'FILE "album/CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "album/CD1/Artist - Album - 02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release = {
        "root": str(parent_root),
        "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        "auxiliary": {"cues": ["Sibling.cue"]},
    }
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                path=str(parent_root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    forged_absolute = root / "Forged.cue"
    release = {
        "root": str(root),
        "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        "auxiliary": {"cues": []},
    }
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[
                    str(track_one),
                    str(track_two),
                    str(forged_absolute),
                ],
                music_release=release,
            )
        )
        is False
    )

    wrong_reference = root / "Wrong.cue"
    wrong_reference.write_text(
        'FILE "CD2/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "CD2/Artist - Album - 02.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["Wrong.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    bare_cue = root / "Bare.cue"
    bare_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nFILE "CD1/Artist - Album - 02.flac" WAVE\n'
    )
    release["auxiliary"] = {"cues": ["Bare.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    partial_cue = root / "Partial.cue"
    partial_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["Partial.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    malformed_cue = root / "Malformed.cue"
    malformed_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac"\nFILE "CD1/Artist - Album - 02.flac"\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["Malformed.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    invalid_structure = root / "InvalidStructure.cue"
    invalid_structure.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nFILE "CD1/Artist - Album - 02.flac" WAVE\nTRACK 01 BROKEN\nTRACK 02 AUDIO\nINDEX 01 00:00\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["InvalidStructure.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    _assert_invalid_cue_cases(root, release, track_one, track_two)

    valid_frame_cue = root / "ValidFrame.cue"
    valid_frame_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:74\nFILE "CD1/Artist - Album - 02.flac" WAVE\nTRACK 02 AUDIO\nINDEX 01 00:00:00\n'
    )
    release["auxiliary"] = {"cues": ["ValidFrame.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is True
    )

    release["auxiliary"] = {"cues": ["Album.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(track_one),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                path=str(root / "missing-release"),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    release["auxiliary"] = {"cues": ["etc/passwd"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    release["auxiliary"] = {"cues": ["../Outside.cue"]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    outside = tmp_path / "Outside.cue"
    outside.touch()
    release["auxiliary"] = {"cues": [str(outside)]}
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two)],
                music_release=release,
            )
        )
        is False
    )

    assert (
        _check(
            _music_meta(
                filelist=["01.flac", "02.flac", "../Outside.cue"],
                music_release={},
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                filelist=["01.flac", "02.flac", str(outside)], music_release={}
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                filelist=[str(track_one), str(track_two), "forged.cue"],
                music_release={"root": "/"},
            )
        )
        is False
    )
    assert (
        _check(
            _music_meta(
                filelist=[str(track_one), str(track_two), "forged.cue"],
                music_release={"root": str(attacker_root)},
            )
        )
        is False
    )

    (root / "Linked.cue").symlink_to(outside)
    release = {
        "root": str(root),
        "tracks": [{"format": "FLAC"}, {"format": "FLAC"}],
        "auxiliary": {"cues": []},
    }
    assert (
        _check(
            _music_meta(
                path=str(root),
                filelist=[str(track_one), str(track_two), "Linked.cue"],
                music_release=release,
            )
        )
        is False
    )

    real_cue = root / "Real.cue"
    real_cue.write_text(
        'FILE "CD1/Artist - Album - 01.flac" WAVE\nFILE "CD1/Artist - Album - 02.flac" WAVE\nTRACK 01 AUDIO\nTRACK 02 AUDIO\nINDEX 01 00:00:00\nINDEX 01 00:00:00\n'
    )
    linked_inside = root / "LinkedInside.cue"
    try:
        linked_inside.symlink_to(real_cue)
    except OSError:
        pass
    else:
        assert (
            _check(
                _music_meta(
                    path=str(root),
                    filelist=[
                        str(track_one),
                        str(track_two),
                        str(linked_inside),
                    ],
                    music_release=release,
                )
            )
            is False
        )

    outside_audio = tmp_path / "Outside.flac"
    outside_audio.touch()
    linked_audio = root / "Linked.flac"
    try:
        linked_audio.symlink_to(outside_audio)
    except OSError:
        pass
    else:
        linked_audio_cue = root / "LinkedAudio.cue"
        linked_audio_cue.write_text(
            'FILE "CD1/Artist - Album - 01.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\nFILE "Linked.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n'
        )
        release["auxiliary"] = {"cues": ["LinkedAudio.cue"]}
        assert (
            _check(
                _music_meta(
                    path=str(root),
                    filelist=[
                        str(track_one),
                        str(linked_audio),
                        str(linked_audio_cue),
                    ],
                    music_release=release,
                )
            )
            is False
        )


def test_railgunpt_does_not_misclassify_payload_names_as_attachments():
    files = ["Submarine.2023.1080p.BluRay.x264-GRP.mkv", "subtitles.rar"]
    assert _check(_movie_meta(filelist=files)) is True
