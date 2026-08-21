from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain_models.processing import ItemProcessingError, NoAudioMediaError
from src.domain_models.release import Meta
from src.services import preparation_helpers as helpers


def test_xxx_store_urls_and_search_year_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "WowGirls.XXX.1080p.mkv"
    video.write_bytes(b"video")
    assert helpers.is_xxx_video_release(video)
    audio = tmp_path / "WowGirls.XXX.mp3"
    audio.write_bytes(b"audio")
    assert not helpers.is_xxx_video_release(audio)

    release = tmp_path / "CherryPimps.XXX.Release"
    release.mkdir()
    (release / "scene.mp4").write_bytes(b"video")
    assert helpers.is_xxx_video_release(release)
    clean = tmp_path / "Clean.Release"
    clean.mkdir()
    (clean / "scene.mp4").write_bytes(b"video")
    assert not helpers.is_xxx_video_release(clean)
    assert not helpers.is_xxx_video_release(tmp_path / "missing")

    assert helpers._is_igdb_url("https://www.igdb.com/games/example")
    assert helpers._is_igdb_url("https://igdb.com/games/example")
    assert not helpers._is_igdb_url("https://example.invalid/igdb.com")
    assert helpers._is_steam_app_url(
        "https://store.steampowered.com/app/123/example"
    )
    assert not helpers._is_steam_app_url(
        "https://store.steampowered.com/sub/123"
    )
    assert helpers._nfo_has_store_link(
        "Store: https://store.steampowered.com/app/123/example"
    )
    assert helpers._nfo_has_store_link(
        "IGDB https://www.igdb.com/games/example"
    )
    assert not helpers._nfo_has_store_link("https://example.invalid/app/123")

    monkeypatch.setattr(
        helpers,
        "urlparse",
        lambda _value: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert not helpers._is_igdb_url("bad")
    assert not helpers._is_steam_app_url("bad")

    assert helpers._normalize_search_year(None) is None
    assert helpers._normalize_search_year("") is None
    assert helpers._normalize_search_year(["bad", "Released 2024"]) == "2024"
    assert helpers._normalize_search_year(("bad",)) is None
    assert helpers._normalize_search_year("1899 edition") == "1899"
    assert helpers._normalize_search_year("year 1799") is None


def test_pre_release_and_title_article_helpers() -> None:
    assert helpers.check_pre_release(Meta(type="CAM"))
    assert helpers.check_pre_release(Meta(source="TELESYNC"))
    assert helpers.check_pre_release(Meta(type="HDCAMRIP"))
    assert not helpers.check_pre_release(Meta(type="WEBDL", source="WEB"))
    assert helpers._title_without_leading_article("The Movie") == "movie"
    assert helpers._tvdb_title_drops_existing_leading_article(
        "The Office", "Office"
    )
    assert not helpers._tvdb_title_drops_existing_leading_article(
        None, "Office"
    )
    assert not helpers._tvdb_title_drops_existing_leading_article(
        "Office", "Office"
    )


def test_calculate_source_size_files_disc_missing_and_stat_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one = tmp_path / "one.bin"
    two = tmp_path / "two.bin"
    one.write_bytes(b"123")
    two.write_bytes(b"12345")
    meta = Meta(filelist=[str(one), str(two), str(tmp_path / "missing")])
    helpers.calculate_source_size(None, meta, "")
    assert meta.source_size == 8

    fallback = Meta(filelist=[])
    helpers.calculate_source_size(None, fallback, str(one))
    assert fallback.source_size == 3

    original_stat = Path.stat

    def flaky_stat(path: Path, *args, **kwargs):
        if path.name == "two.bin":
            raise OSError("stat failed")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    meta = Meta(filelist=[str(one), str(two)])
    helpers.calculate_source_size(None, meta, "")
    assert meta.source_size == 3
    monkeypatch.setattr(Path, "stat", original_stat)

    disc = tmp_path / "disc"
    disc.mkdir()
    (disc / "a.bin").write_bytes(b"1234")
    (disc / "b.bin").write_bytes(b"12")
    meta = Meta(is_disc="BDMV", path=str(disc))
    helpers.calculate_source_size(None, meta, "")
    assert meta.source_size == 6

    missing_disc = Meta(is_disc="BDMV", path=str(tmp_path / "missing-disc"))
    helpers.calculate_source_size(None, missing_disc, "")
    assert missing_disc.source_size == 0


def _validation_meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "validation",
        "path": str(tmp_path / "release.mkv"),
        "category": "MOVIE",
        "mediainfo": {"media": {"track": []}},
        "unattended": True,
        "unattended_confirm": False,
    }
    state.update(values)
    return Meta(state)


def test_validate_media_conformance_prompt_cleanup_and_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / "tmp" / "validation"
    temp.mkdir(parents=True)
    (temp / "meta.json").write_text("{}", encoding="utf-8")
    (temp / "keep.bin").write_bytes(b"x")

    async def issues(_meta: Meta):
        return ["bad"]

    monkeypatch.setattr(helpers, "get_conformance_error", issues)
    monkeypatch.setattr(
        helpers.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False
    )
    meta = _validation_meta(tmp_path, unattended=False)
    with pytest.raises(ItemProcessingError, match="Conformance errors"):
        asyncio.run(helpers.validate_media(None, meta))
    assert not (temp / "meta.json").exists()
    assert (temp / "keep.bin").exists()

    cleanup = AsyncMock()
    monkeypatch.setattr(helpers.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(
        helpers.cleanup_manager, "reset_terminal", lambda: None
    )
    monkeypatch.setattr(
        helpers.cli_ui,
        "ask_yes_no",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()),
    )
    meta = _validation_meta(tmp_path, unattended=False)
    with pytest.raises(ItemProcessingError, match="skipped by user"):
        asyncio.run(helpers.validate_media(None, meta))
    cleanup.assert_awaited_once()

    monkeypatch.setattr(
        helpers.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(helpers, "validate_mediainfo", lambda _meta: True)
    meta = _validation_meta(tmp_path, unattended_confirm=True)
    asyncio.run(helpers.validate_media(None, meta))
    assert meta.valid_mi is True


def test_validate_media_invalid_mediainfo_errors_subtitles_and_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_issues(_meta: Meta):
        return []

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(helpers, "get_conformance_error", no_issues)
    monkeypatch.setattr(helpers.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(helpers, "validate_mediainfo", lambda _meta: False)
    meta = _validation_meta(
        tmp_path,
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Text", "Default": "Yes"},
                    {"@type": "Text", "Default": "Yes"},
                ]
            }
        },
    )
    asyncio.run(helpers.validate_media(None, meta))
    assert meta.valid_mi is False
    assert meta.has_multiple_default_subtitle_tracks is True

    def no_audio(_meta: Meta):
        raise NoAudioMediaError("no audio")

    monkeypatch.setattr(helpers, "validate_mediainfo", no_audio)
    with pytest.raises(NoAudioMediaError, match="does not support no audio"):
        asyncio.run(helpers.validate_media(None, _validation_meta(tmp_path)))

    monkeypatch.setattr(
        helpers,
        "validate_mediainfo",
        lambda _meta: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    with pytest.raises(RuntimeError, match="bad"):
        asyncio.run(helpers.validate_media(None, _validation_meta(tmp_path)))

    monkeypatch.setattr(helpers, "validate_mediainfo", lambda _meta: True)
    monkeypatch.setattr(
        helpers.languages_manager,
        "parsed_mediainfo",
        AsyncMock(return_value={"audio": [{"language": "English"}]}),
    )
    with pytest.raises(Exception, match="Language check failed"):
        asyncio.run(
            helpers.validate_media(
                None, _validation_meta(tmp_path, has_languages="en,pt")
            )
        )
    with pytest.raises(Exception, match="Language check failed"):
        asyncio.run(
            helpers.validate_media(
                None, _validation_meta(tmp_path, has_languages="French")
            )
        )
    asyncio.run(
        helpers.validate_media(
            None, _validation_meta(tmp_path, has_languages="English,French")
        )
    )


class _TorrentClient:
    def __init__(
        self, path: str | None = None, *, includes_subtitles: bool = False
    ) -> None:
        self.path = path
        self.includes_subtitles = includes_subtitles
        self.properties: list[tuple[bool, str | None]] = []

    async def find_existing_torrent(self, _meta: Meta) -> str | None:
        return self.path

    def _torrent_includes_all_local_subtitles(
        self, _path: str, _meta: Meta
    ) -> bool:
        return self.includes_subtitles

    async def get_ptp_from_hash(
        self, _meta: Meta, *, pathed: bool, client_name: str | None
    ) -> None:
        self.properties.append((pathed, client_name))


def test_process_trackers_defaults_strings_existing_torrent_and_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prep = SimpleNamespace(
        config={"TRACKERS": {"default_trackers": "aither, bhd"}}
    )
    torrent = tmp_path / "existing.torrent"
    torrent.write_bytes(b"d4:infod4:name4:teste")
    client = _TorrentClient(str(torrent), includes_subtitles=True)
    monkeypatch.setattr(
        helpers.Torrent,
        "read",
        lambda _path: SimpleNamespace(infohash="abc123"),
    )
    meta = Meta(
        trackers=[],
        tracker_ids={},
        edit=False,
        subtitle_files=["subtitle.srt"],
        reuse_torrent_client="qbit",
    )
    asyncio.run(
        helpers.process_trackers_and_torrent(
            prep, meta, client, ["infohash"], [], "", ""
        )
    )
    assert meta.description == ""
    assert meta.trackers == ["AITHER", "BHD"]
    assert meta.requested_trackers == ["AITHER", "BHD"]
    assert meta.subs_reuse_torrent_path == str(torrent)
    assert meta.infohash
    assert client.properties == [(True, "qbit")]

    client = _TorrentClient(
        str(tmp_path / "invalid.torrent"), includes_subtitles=False
    )
    monkeypatch.setattr(
        helpers.Torrent,
        "read",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid")),
    )
    meta = Meta(trackers="aither", tracker_ids={}, edit=False)
    asyncio.run(
        helpers.process_trackers_and_torrent(
            prep, meta, client, ["infohash"], [], "", ""
        )
    )
    assert meta.trackers == ["AITHER"]
    assert meta.base_reuse_torrent_path.endswith("invalid.torrent")

    meta = Meta(
        trackers=[" aither ", "bhd"],
        infohash="already",
        tracker_ids={},
        edit=False,
    )
    client = _TorrentClient(str(torrent))
    asyncio.run(
        helpers.process_trackers_and_torrent(
            prep, meta, client, ["infohash"], [], "", ""
        )
    )
    assert client.properties == []


class _DiscInfo:
    def __init__(
        self,
        result: tuple[str, str, dict, list] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error

    async def get_disc(self, _meta: Meta):
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


def _detection_prep(
    path: Path,
    *,
    disc: str = "",
    bdinfo: dict | None = None,
    discs: list | None = None,
):
    return SimpleNamespace(
        disc_info_manager=_DiscInfo(
            (disc, str(path), bdinfo or {}, discs or [])
        )
    )


def test_detect_disc_manual_book_game_package_and_audio_categories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        iso = tmp_path / "Game.iso"
        iso.write_bytes(b"game")
        meta = Meta(path=str(iso))
        location, _ = await helpers.detect_disc_and_category(
            _detection_prep(iso), meta
        )
        assert location == str(iso) and meta.category == "GAME"

        game_dir = tmp_path / "Game.Release"
        game_dir.mkdir()
        (game_dir / "setup.exe").write_bytes(b"game")
        meta = Meta(path=str(game_dir))
        await helpers.detect_disc_and_category(_detection_prep(game_dir), meta)
        assert meta.category == "GAME"

        m4b = tmp_path / "book.m4b"
        m4b.write_bytes(b"audio")
        meta = Meta(path=str(m4b), manual_category=" book ")
        await helpers.detect_disc_and_category(_detection_prep(m4b), meta)
        assert meta.category == "BOOK" and meta.audiobook is True

        book_dir = tmp_path / "Audiobook"
        book_dir.mkdir()
        (book_dir / "part.mp3").write_bytes(b"audio")
        meta = Meta(path=str(book_dir), category="BOOK")
        await helpers.detect_disc_and_category(_detection_prep(book_dir), meta)
        assert meta.audiobook is True

        from src.services import audio_classification_service

        monkeypatch.setattr(
            audio_classification_service,
            "detect_audio_category",
            AsyncMock(
                return_value=SimpleNamespace(
                    category="MUSIC",
                    is_audiobook=False,
                    evidence=["music tags"],
                )
            ),
        )
        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"audio")
        meta = Meta(path=str(mp3))
        await helpers.detect_disc_and_category(_detection_prep(mp3), meta)
        assert meta.category == "MUSIC" and meta.audiobook is False

    asyncio.run(exercise())


def test_detect_ambiguous_audio_choices_cancellation_and_unattended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services import audio_classification_service

    audio = tmp_path / "unknown.mp3"
    audio.write_bytes(b"audio")
    result = SimpleNamespace(
        category="AMBIGUOUS", is_audiobook=False, evidence=["mixed"]
    )
    monkeypatch.setattr(
        audio_classification_service,
        "detect_audio_category",
        AsyncMock(return_value=result),
    )

    async def detect(
        choice: object, *, unattended: bool = False, confirm: bool = False
    ) -> Meta:
        monkeypatch.setattr(
            helpers.cli_ui, "ask_choice", lambda *_args, **_kwargs: choice
        )
        meta = Meta(
            path=str(audio), unattended=unattended, unattended_confirm=confirm
        )
        await helpers.detect_disc_and_category(_detection_prep(audio), meta)
        return meta

    assert asyncio.run(detect("1. Music")).category == "MUSIC"
    audiobook = asyncio.run(detect("audiobook"))
    assert audiobook.category == "BOOK" and audiobook.audiobook is True
    assert asyncio.run(detect("3. Podcast")).category == "PODCAST"
    assert (
        asyncio.run(detect("music", unattended=True, confirm=True)).category
        == "MUSIC"
    )

    with pytest.raises(ItemProcessingError, match="interactive selection"):
        asyncio.run(detect(None))
    monkeypatch.setattr(
        helpers.cli_ui,
        "ask_choice",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()),
    )
    with pytest.raises(ItemProcessingError, match="interactive cancellation"):
        asyncio.run(
            helpers.detect_disc_and_category(
                _detection_prep(audio), Meta(path=str(audio))
            )
        )
    with pytest.raises(ItemProcessingError, match="mixed audio signals"):
        asyncio.run(
            helpers.detect_disc_and_category(
                _detection_prep(audio),
                Meta(
                    path=str(audio), unattended=True, unattended_confirm=False
                ),
            )
        )


def test_detect_fallback_book_game_nfo_group_and_xxx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services import audio_classification_service

    monkeypatch.setattr(
        audio_classification_service,
        "detect_audio_category",
        AsyncMock(
            return_value=SimpleNamespace(
                category="NONE", is_audiobook=False, evidence=[]
            )
        ),
    )

    async def exercise() -> None:
        ebook = tmp_path / "book.epub"
        ebook.write_bytes(b"book")
        meta = Meta(path=str(ebook))
        await helpers.detect_disc_and_category(_detection_prep(ebook), meta)
        assert meta.category == "BOOK"

        books = tmp_path / "books"
        books.mkdir()
        (books / "book.pdf").write_bytes(b"book")
        (books / "readme.txt").write_text("sidecar", encoding="utf-8")
        meta = Meta(path=str(books))
        await helpers.detect_disc_and_category(_detection_prep(books), meta)
        assert meta.category == "BOOK"

        mixed = tmp_path / "mixed"
        mixed.mkdir()
        (mixed / "book.pdf").write_bytes(b"book")
        (mixed / "movie.mkv").write_bytes(b"video")
        meta = Meta(path=str(mixed))
        await helpers.detect_disc_and_category(_detection_prep(mixed), meta)
        assert meta.category != "BOOK"

        game = tmp_path / "Title-RUNE"
        game.mkdir()
        (game / "payload.bin").write_bytes(b"game")
        meta = Meta(path=str(game))
        await helpers.detect_disc_and_category(_detection_prep(game), meta)
        assert meta.category == "GAME"

        nfo_game = tmp_path / "Store.Game"
        nfo_game.mkdir()
        (nfo_game / "release.nfo").write_text(
            "https://store.steampowered.com/app/123/game", encoding="utf-8"
        )
        meta = Meta(path=str(nfo_game))
        await helpers.detect_disc_and_category(_detection_prep(nfo_game), meta)
        assert meta.category == "GAME"

        xxx = tmp_path / "WowGirls.XXX.Release.mkv"
        xxx.write_bytes(b"video")
        meta = Meta(path=str(xxx))
        await helpers.detect_disc_and_category(_detection_prep(xxx), meta)
        assert meta.category == "XXX"

    asyncio.run(exercise())


def test_detect_disc_error_is_propagated(tmp_path: Path) -> None:
    prep = SimpleNamespace(
        disc_info_manager=_DiscInfo(error=RuntimeError("disc failed"))
    )
    with pytest.raises(RuntimeError, match="disc failed"):
        asyncio.run(
            helpers.detect_disc_and_category(prep, Meta(path=str(tmp_path)))
        )


class _Scene:
    def __init__(self, video: str = "Scene.Release.mkv") -> None:
        self.video = video

    async def is_scene(self, _path: str, _meta: Meta, imdb_id: object):
        return self.video, True, imdb_id or 123


class _Names:
    def __init__(
        self,
        result=("Example Title", "Secondary", 2024),
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error

    async def extract_title_and_year(self, _meta: Meta, _video: str):
        if self.error:
            raise self.error
        return self.result


class _DiscSize:
    async def get_dvd_size(self, _discs, _manual):
        return "DVD9"


class _MediaPrep:
    def __init__(
        self,
        *,
        name_result=("Example Title", "Secondary", 2024),
        name_error=None,
        video="Scene.Release.mkv",
    ) -> None:
        self.scene_manager = _Scene(video)
        self.name_manager = _Names(name_result, name_error)
        self.disc_info_manager = _DiscSize()
        self.book_calls = 0
        self.game_calls = 0

    def _resolve_book_filelist(self, meta: Meta, location: str):
        meta.filelist = [location]
        return location, [location], Path(location).name, "file"

    def _resolve_game_filelist(self, meta: Meta, location: str):
        meta.filelist = [location]
        return location, [location], Path(location).name, "file"

    async def _gather_book_prep(
        self, _meta: Meta, _videopath: str, _base_dir: str
    ):
        self.book_calls += 1

    async def _gather_game_prep(
        self, _meta: Meta, _videopath: str, _base_dir: str
    ):
        self.game_calls += 1


def _media_meta(tmp_path: Path, **values: object) -> Meta:
    path = tmp_path / "Release.2024.1080p.mkv"
    path.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "media",
        "path": str(path),
        "mode": "cli",
        "isdir": False,
        "category": "MOVIE",
        "sorted_filelist": False,
        "mediainfo": {"media": {"track": []}},
    }
    state.update(values)
    return Meta(state)


def test_process_media_files_bluray_title_fallback_hfr_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def resolution(*_args, **_kwargs):
        return "1080p"

    async def sd(_value):
        return 0

    monkeypatch.setattr(helpers, "mi_resolution", resolution)
    monkeypatch.setattr(helpers.video_manager, "is_sd", sd)
    monkeypatch.setattr(
        helpers,
        "guessit_fn",
        lambda _value, _options=None: {"title": "Guessed", "year": 2023},
    )

    meta = _media_meta(tmp_path, is_disc="BDMV", resolution="")
    bdinfo = {
        "title": "Disc-Title-2024",
        "label": "Disc-Label",
        "video": [{"fps": "60.000", "res": "1080p"}],
    }
    result = asyncio.run(
        helpers.process_media_files(_MediaPrep(), meta, str(tmp_path), bdinfo)
    )
    assert result[0] == "Example Title"
    assert meta.secondary_title == "Secondary" and meta.year == 2024
    assert (
        meta.hfr is True
        and meta.search_year == 2023
        and meta.resolution == "1080p"
    )

    meta = _media_meta(tmp_path, is_disc="BDMV", resolution="")
    prep = _MediaPrep(name_result=("", "", None))
    result = asyncio.run(
        helpers.process_media_files(prep, meta, str(tmp_path), bdinfo)
    )
    assert result[0] == "Guessed"

    meta = _media_meta(tmp_path, is_disc="BDMV", resolution="1080p")
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(name_error=RuntimeError("name failed")),
            meta,
            str(tmp_path),
            bdinfo,
        )
    )
    assert result[0] == "Guessed"

    bad_bdinfo = {
        "title": "Title",
        "label": "Label",
        "video": [{"fps": "bad", "res": "1080p"}],
    }
    meta = _media_meta(tmp_path, is_disc="BDMV", resolution="1080p")
    asyncio.run(
        helpers.process_media_files(
            _MediaPrep(), meta, str(tmp_path), bad_bdinfo
        )
    )
    assert meta.hfr is False


def test_process_media_files_dvd_and_hddvd_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def export(*_args, **_kwargs):
        return {"media": {"track": []}}

    async def resolution(*_args, **_kwargs):
        return "576p", False

    async def sd(_value):
        return 1

    monkeypatch.setattr(helpers, "export_info", export)
    monkeypatch.setattr(helpers.video_manager, "get_resolution", resolution)
    monkeypatch.setattr(helpers.video_manager, "is_sd", sd)
    monkeypatch.setattr(
        helpers,
        "guessit_fn",
        lambda _value, _options=None: {"title": "Guessed DVD", "year": 2001},
    )

    dvd_root = tmp_path / "DVD-2001" / "VIDEO_TS"
    dvd_root.mkdir(parents=True)
    meta = _media_meta(
        tmp_path,
        is_disc="DVD",
        discs=[{"path": str(dvd_root), "main_set": ["01"]}],
        path=str(dvd_root.parent),
        edit=False,
    )
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(name_result=("", "", None)),
            meta,
            str(dvd_root.parent),
            {},
        )
    )
    assert result[0] == "Guessed DVD"
    assert meta.dvd_size == "DVD9" and meta.sd == 1 and meta.mediainfo

    meta = _media_meta(
        tmp_path,
        is_disc="DVD",
        discs=[{"path": str(dvd_root), "main_set": ["01"]}],
        path=str(dvd_root.parent),
        edit=True,
        mediainfo={"cached": True},
    )
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(), meta, str(dvd_root.parent), {}
        )
    )
    assert result[5] == {"cached": True}

    evo = tmp_path / "largest.evo"
    evo.write_bytes(b"video")
    meta = _media_meta(
        tmp_path,
        is_disc="HDDVD",
        discs=[
            {"path": str(tmp_path / "HD-DVD-2002"), "largest_evo": str(evo)}
        ],
        edit=False,
    )
    result = asyncio.run(
        helpers.process_media_files(_MediaPrep(), meta, str(tmp_path), {})
    )
    assert result[2] == str(evo)
    assert meta.search_year == 2001


def test_process_media_files_book_game_and_video_subtitles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prep = _MediaPrep(video="Book.Scene.mkv")
    monkeypatch.setattr(
        helpers,
        "guessit_fn",
        lambda _value, _options=None: {"title": "Guessed", "year": 2024},
    )

    book = tmp_path / "book.epub"
    book.write_bytes(b"book")
    meta = _media_meta(tmp_path, path=str(book), category="BOOK")
    result = asyncio.run(
        helpers.process_media_files(prep, meta, str(book), {})
    )
    assert prep.book_calls == 1 and result[2] == str(book)

    game = tmp_path / "game.iso"
    game.write_bytes(b"game")
    meta = _media_meta(tmp_path, path=str(game), category="GAME")
    result = asyncio.run(
        helpers.process_media_files(prep, meta, str(game), {})
    )
    assert prep.game_calls == 1 and meta.filename

    release = tmp_path / "Release"
    release.mkdir()
    video = release / "Movie.2024.mkv"
    video.write_bytes(b"video")
    (release / "Movie.2024.en.srt").write_text("subtitle", encoding="utf-8")
    (release / "other.srt").write_text("subtitle", encoding="utf-8")
    bdmv = release / "BDMV"
    bdmv.mkdir()
    (bdmv / "ignored.srt").write_text("subtitle", encoding="utf-8")

    async def get_video(_location, _mode, _sorted):
        return str(video), [str(video)]

    async def export(_path, *_args, **_kwargs):
        return {"media": {"track": []}}

    async def resolution(*_args, **_kwargs):
        return "1080p", False

    async def sd(_value):
        return 0

    monkeypatch.setattr(helpers.video_manager, "get_video", get_video)
    monkeypatch.setattr(helpers, "export_info", export)
    monkeypatch.setattr(helpers.video_manager, "get_resolution", resolution)
    monkeypatch.setattr(helpers.video_manager, "is_sd", sd)
    meta = _media_meta(
        tmp_path, path=str(release), isdir=True, category="MOVIE", edit=False
    )
    asyncio.run(
        helpers.process_media_files(_MediaPrep(), meta, str(release), {})
    )
    assert str(release / "Movie.2024.en.srt") in meta.subtitle_files
    assert all("BDMV" not in item for item in meta.subtitle_files)

    meta = _media_meta(
        tmp_path, path=str(video), isdir=False, category="MOVIE", edit=True
    )
    asyncio.run(
        helpers.process_media_files(_MediaPrep(), meta, str(video), {})
    )
    assert meta.subtitle_files == [str(release / "Movie.2024.en.srt")]


def test_process_media_files_name_and_metadata_errors_are_semantic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"video")

    async def get_video(_location, _mode, _sorted):
        return str(video), [str(video)]

    monkeypatch.setattr(helpers.video_manager, "get_video", get_video)
    meta = _media_meta(tmp_path, path=str(video), category="MOVIE")
    with pytest.raises(Exception, match="Error extracting title and year"):
        asyncio.run(
            helpers.process_media_files(
                _MediaPrep(name_error=RuntimeError("bad")),
                meta,
                str(video),
                {},
            )
        )

    prep = _MediaPrep(name_result=("Title", "", None))

    async def media_error(*_args, **_kwargs):
        from src.domain_models.errors import MediaInfoError

        raise MediaInfoError("bad", command=["mediainfo"], stderr="details")

    monkeypatch.setattr(helpers, "export_info", media_error)
    with pytest.raises(
        ItemProcessingError, match="MediaInfo could not inspect"
    ):
        asyncio.run(
            helpers.process_media_files(
                prep,
                _media_meta(
                    tmp_path, path=str(video), category="MOVIE", edit=False
                ),
                str(video),
                {},
            )
        )


class _SearchManager:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sonarr_result: dict | None = None
        self.radarr_result: dict | None = None
        self.tmdb_id_result: tuple[int, str] = (0, "MOVIE")
        self.tmdb_from_imdb_result: tuple[str, int, str, bool] = (
            "MOVIE",
            0,
            "en",
            False,
        )
        self.mediainfo_ids: tuple[str, int, int, int] = ("MOVIE", 0, 0, 0)

    async def get_sonarr_data(self, **_kwargs):
        self.calls.append("sonarr")
        return self.sonarr_result

    async def get_radarr_data(self, **_kwargs):
        self.calls.append("radarr")
        return self.radarr_result

    async def get_tracker_data(self, *_args, **_kwargs):
        self.calls.append("tracker_data")

    async def ping_unit3d(self, _meta):
        self.calls.append("ping")

    async def get_source_override(self, meta, **_kwargs):
        self.calls.append("override")
        return meta

    async def get_season_episode(self, _path, meta):
        self.calls.append("season")
        return meta

    async def get_tmdb_imdb_from_mediainfo(self, _mi, _meta):
        self.calls.append("mediainfo_ids")
        return self.mediainfo_ids

    async def get_tmdb_id(self, *_args, **_kwargs):
        self.calls.append("tmdb_id")
        return self.tmdb_id_result

    async def get_tmdb_from_imdb(self, *_args, **_kwargs):
        self.calls.append("tmdb_from_imdb")
        return self.tmdb_from_imdb_result

    async def set_tmdb_metadata(self, _meta, _filename):
        self.calls.append("set_tmdb")

    async def all_ids(self, meta):
        self.calls.append("all_ids")
        return meta

    async def imdb_tmdb_tvdb(self, meta, _filename):
        self.calls.append("imdb_tmdb_tvdb")
        return meta

    async def imdb_tvdb(self, meta, _filename):
        self.calls.append("imdb_tvdb")
        return meta

    async def imdb_tmdb(self, meta, _filename):
        self.calls.append("imdb_tmdb")
        return meta


class _SearchClient:
    def __init__(self) -> None:
        self.called = 0

    async def get_ptp_from_hash(self, meta: Meta):
        self.called += 1
        return meta


def _search_prep(manager: _SearchManager, *, config: dict | None = None):
    return SimpleNamespace(
        config=config
        or {
            "DEFAULT": {
                "ping_unit3d": False,
                "user_overrides": False,
            }
        },
        get_cat=AsyncMock(return_value="MOVIE"),
        sonarr_manager=manager,
        radarr_manager=manager,
        tracker_data_manager=manager,
        overrides=manager,
        season_episode_manager=manager,
        tmdb_manager=manager,
        metadata_searching_manager=manager,
    )


def _search_meta(tmp_path: Path, **values: object) -> Meta:
    path = tmp_path / "Release.2024.1080p.mkv"
    path.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "Release.2024.1080p",
        "path": str(path),
        "filename": path.name,
        "category": "MOVIE",
        "tmdb_id": 0,
        "imdb_id": 0,
        "tvdb_id": 0,
        "tvmaze_id": 0,
        "mal_id": 0,
        "manual_year": 0,
        "year": 2024,
        "search_year": 2024,
        "infohash": None,
        "base_torrent_created": False,
        "we_checked_them_all": False,
        "skip_trackers": True,
        "matched_tracker": False,
        "edit": False,
        "unattended": True,
        "unattended_confirm": False,
        "mediainfo": {"media": {"track": []}},
        "imdb_info": {},
        "trackers": [],
    }
    state.update(values)
    return Meta(state)


def _patch_search_globals(
    monkeypatch: pytest.MonkeyPatch, *, imdb_search: object = 0
) -> None:
    monkeypatch.setattr(
        helpers.video_manager, "get_type", AsyncMock(return_value="WEBDL")
    )
    monkeypatch.setattr(
        helpers.video_manager,
        "get_video_duration",
        AsyncMock(return_value=120),
    )
    monkeypatch.setattr(
        helpers.imdb_manager,
        "search_imdb",
        AsyncMock(return_value=imdb_search),
    )
    monkeypatch.setattr(
        helpers.imdb_manager,
        "get_imdb_info_api",
        AsyncMock(return_value={"title": "IMDb Title"}),
    )


def test_search_metadata_manual_ids_missing_category_book_game_and_invalid_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_search_globals(monkeypatch)
    manager = _SearchManager()
    prep = _search_prep(manager)
    prep.get_cat = AsyncMock(return_value="BOOK")
    meta = _search_meta(
        tmp_path,
        category="",
        tmdb_manual="bad",
        imdb_manual="tt1234567",
        mal_manual="bad",
        tvdb_manual="bad",
        tvmaze_manual="tvmaze",
        path=str(tmp_path / "book.cbz"),
    )
    Path(meta.path).write_bytes(b"book")
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Book",
            "Book",
            str(meta.path),
            "Book",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert meta.category == "BOOK"
    assert (
        meta.tmdb_id == 0
        and meta.imdb_id == 1234567
        and meta.mal_id == 0
        and meta.tvdb_id == 0
    )
    assert meta.tvmaze_id == "tvmaze"
    assert meta.type == "CBZ" and meta.comic is True

    game = _search_meta(
        tmp_path, category="GAME", path=str(tmp_path / "game.iso")
    )
    Path(game.path).write_bytes(b"game")
    asyncio.run(
        helpers.search_metadata(
            prep,
            game,
            "Game",
            "Game",
            game.path,
            "Game",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert game.type == "GAME"


def test_search_metadata_sonarr_radarr_tracker_hash_ping_override_and_anime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_search_globals(monkeypatch)
    manager = _SearchManager()
    manager.sonarr_result = {
        "tvdb_id": 101,
        "imdb_id": 202,
        "tvmaze_id": 303,
        "tmdb_id": 404,
        "genres": ["Drama"],
        "release_group": "GROUP",
        "year": 2020,
    }
    prep = _search_prep(manager)
    meta = _search_meta(
        tmp_path,
        category="TV",
        skip_trackers=False,
        tvdb_id=0,
        manual_year=0,
        not_anime=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Show",
            "Show",
            meta.path,
            "Show",
            "file",
            True,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert (meta.tvdb_id, meta.imdb_id, meta.tvmaze_id, meta.tmdb_id) == (
        101,
        202,
        303,
        404,
    )
    assert meta.manual_year == 2020 and meta.not_anime is True

    manager = _SearchManager()
    manager.radarr_result = {
        "imdb_id": 11,
        "tmdb_id": 22,
        "genres": ["Action"],
        "year": 2021,
        "release_group": "GROUP",
    }
    prep = _search_prep(manager)
    meta = _search_meta(tmp_path, category="MOVIE", skip_trackers=False)
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            True,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert (
        meta.imdb_id == 11 and meta.tmdb_id == 22 and meta.manual_year == 2021
    )

    manager = _SearchManager()
    manager.mediainfo_ids = ("MOVIE", 77, 123, 0)
    prep = _search_prep(
        manager,
        config={"DEFAULT": {"ping_unit3d": True, "user_overrides": True}},
    )
    client = _SearchClient()
    meta = _search_meta(
        tmp_path,
        category="MOVIE",
        skip_trackers=False,
        infohash="hash",
        is_disc="BDMV",
        region="",
        distributor="",
        imdb_id=123,
        mal_id=456,
        manual_language="Portuguese",
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            False,
            False,
            client,
            {},
            {},
        )
    )
    assert client.called == 1
    assert {"tracker_data", "ping", "override", "set_tmdb"} <= set(
        manager.calls
    )
    assert meta.anime is True and meta.not_anime is True
    assert meta.original_language == "portuguese"


def test_search_metadata_sonarr_radarr_second_lookup_and_season(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_search_globals(monkeypatch)
    ids = {
        "tvdb_id": 9,
        "imdb_id": 8,
        "tvmaze_id": 7,
        "tmdb_id": 6,
        "genres": ["Drama"],
        "year": 2022,
        "release_group": "G",
    }
    manager = _SearchManager()
    manager.sonarr_result = ids
    prep = _search_prep(manager)
    meta = _search_meta(
        tmp_path,
        category="TV",
        skip_trackers=False,
        tvdb_id=9,
        matched_tracker=False,
        manual_date="2024-01-01",
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Show",
            "Show",
            meta.path,
            "Show",
            "file",
            True,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert "sonarr" in manager.calls and "season" in manager.calls

    manager = _SearchManager()
    manager.radarr_result = {
        "imdb_id": 8,
        "tmdb_id": 6,
        "genres": ["Drama"],
        "year": 2022,
        "release_group": "G",
    }
    prep = _search_prep(manager)
    meta = _search_meta(
        tmp_path,
        category="MOVIE",
        skip_trackers=False,
        tmdb_id=6,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            True,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert "radarr" in manager.calls


def test_search_metadata_no_ids_imdb_conversion_and_combination_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _SearchManager()
    manager.mediainfo_ids = ("MOVIE", 0, 0, 0)
    manager.tmdb_id_result = (123, "MOVIE")
    prep = _search_prep(manager)
    _patch_search_globals(monkeypatch, imdb_search=456)
    meta = _search_meta(tmp_path, category="MOVIE", skip_trackers=True)
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert meta.tmdb_id == 123 and meta.imdb_id == 456
    assert meta.quickie_search is True and meta.no_ids is True

    for ids, expected in (
        ((1, 2, 3, 4), "all_ids"),
        ((1, 2, 3, 0), "imdb_tmdb_tvdb"),
        ((1, 0, 3, 0), "imdb_tvdb"),
        ((1, 2, 0, 0), "imdb_tmdb"),
    ):
        manager = _SearchManager()
        prep = _search_prep(manager)
        meta = _search_meta(
            tmp_path,
            category="BOOK" if expected == "imdb_tvdb" else "MOVIE",
            imdb_id=ids[0],
            tmdb_id=ids[1],
            tvdb_id=ids[2],
            tvmaze_id=ids[3],
            original_language=None,
            tvdb_series_name="Bad TVDB Name",
        )
        asyncio.run(
            helpers.search_metadata(
                prep,
                meta,
                "Movie",
                "Movie",
                meta.path,
                "Movie",
                "file",
                False,
                False,
                False,
                _SearchClient(),
                {},
                {},
            )
        )
        assert expected in manager.calls


def test_search_metadata_imdb_to_tmdb_mismatch_imdb_info_and_search_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _SearchManager()
    manager.mediainfo_ids = ("MOVIE", 0, 123, 0)
    manager.tmdb_from_imdb_result = ("MOVIE", 321, "fr", True)
    prep = _search_prep(manager)
    _patch_search_globals(monkeypatch)
    meta = _search_meta(
        tmp_path, category="MOVIE", imdb_id=123, tmdb_id=0, imdb_info={}
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert (
        meta.tmdb_id == 321
        and meta.original_language == "fr"
        and meta.no_ids is True
    )
    assert meta.imdb_info == {"title": "IMDb Title"}

    manager = _SearchManager()
    prep = _search_prep(manager)
    _patch_search_globals(monkeypatch)
    meta = _search_meta(
        tmp_path,
        category="MOVIE",
        imdb_id=123,
        tmdb_id=456,
        imdb_mismatch=True,
        mismatched_imdb_id=999,
        imdb_info={"title": "Old"},
        uuid="regular-release",
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert meta.imdb_id == 999

    monkeypatch.setattr(
        helpers.imdb_manager,
        "search_imdb",
        AsyncMock(side_effect=RuntimeError("imdb failed")),
    )
    meta = _search_meta(tmp_path, category="MOVIE", tmdb_id=0, imdb_id=0)
    manager.mediainfo_ids = ("MOVIE", 0, 0, 0)
    manager.tmdb_id_result = (0, "MOVIE")
    with pytest.raises(RuntimeError, match="imdb failed"):
        asyncio.run(
            helpers.search_metadata(
                prep,
                meta,
                "Movie",
                "Movie",
                meta.path,
                "Movie",
                "file",
                False,
                False,
                False,
                _SearchClient(),
                {},
                {},
            )
        )


def test_init_meta_sets_defaults_directory_mode_and_only_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "Release"
    path.mkdir()
    created: list[dict] = []

    class Client:
        def __init__(self, *, config):
            created.append(config)

    monkeypatch.setattr(helpers, "Clients", Client)
    config = {
        "DEFAULT": {
            "cutoff_screens": "2",
            "skip_auto_torrent": True,
            "use_sonarr": True,
            "use_radarr": True,
            "print_tracker_messages": True,
            "print_tracker_links": False,
            "tracker_description_mode": "text_and_images",
            "mkbrr_threads": "3",
        },
        "TRACKERS": {},
    }
    prep = SimpleNamespace(config=config)
    meta = Meta(
        base_dir=str(tmp_path),
        path=str(path),
        uuid="",
        only_id=True,
        type="CAM",
    )
    result = helpers.init_meta(prep, meta, "cli")
    assert result[:4] == (True, True, result[2], True)
    assert result[4] == ["infohash", "torrent_hash", "skip_auto_torrent"]
    assert "ptp" in result[5]
    assert meta.cutoff == 2 and meta.mode == "cli" and meta.isdir is True
    assert meta.uuid == "Release" and meta.basename_no_ext == "Release"
    assert meta.keep_images is False and meta.skip_tracker_descriptions is True
    assert meta.mkbrr_threads == "3" and meta.pre_release is True
    assert (tmp_path / "tmp" / "Release").is_dir()
    assert created == [config]

    file = tmp_path / "Movie.2024.mkv"
    file.write_bytes(b"video")
    meta = Meta(
        base_dir=str(tmp_path), path=str(file), uuid="file", only_id=False
    )
    result = helpers.init_meta(prep, meta, "batch")
    assert result[3] is False
    assert meta.keep_images is True and meta.basename_no_ext == "Movie.2024"


def test_to_int_process_tracker_description_and_disc_stat_cleanup_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert helpers._to_int("bad", 9) == 9

    prep = SimpleNamespace(config={"TRACKERS": {"default_trackers": ""}})
    meta = Meta(description=None, trackers=[], tracker_ids={}, edit=True)
    asyncio.run(
        helpers.process_trackers_and_torrent(
            prep, meta, _TorrentClient(), [], [], "", ""
        )
    )
    assert meta.description == ""

    disc = tmp_path / "disc-error"
    disc.mkdir()
    file = disc / "bad.bin"
    file.write_bytes(b"data")
    original_stat = Path.stat

    def fail_stat(path: Path, *args, **kwargs):
        if path.name == "bad.bin":
            raise OSError("bad stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    meta = Meta(is_disc="BDMV", path=str(disc))
    helpers.calculate_source_size(None, meta, "")
    assert meta.source_size == 0
    monkeypatch.setattr(Path, "stat", original_stat)

    state = tmp_path / "tmp" / "cleanup"
    state.mkdir(parents=True)
    (state / "meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        helpers, "get_conformance_error", AsyncMock(return_value=["bad"])
    )
    monkeypatch.setattr(
        helpers.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False
    )
    original_iterdir = Path.iterdir

    def fail_iterdir(path: Path):
        if path == state:
            raise OSError("cannot list")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    with pytest.raises(ItemProcessingError, match="Conformance errors"):
        asyncio.run(
            helpers.validate_media(
                None,
                _validation_meta(tmp_path, uuid="cleanup", unattended=False),
            )
        )


def _patch_process_video(monkeypatch: pytest.MonkeyPatch, video: Path) -> None:
    async def get_video(_location, _mode, _sorted):
        return str(video), [str(video)]

    async def export(_path, *_args, **_kwargs):
        return {"media": {"track": []}}

    async def resolution(*_args, **_kwargs):
        return "1080p", False

    async def sd(_value):
        return 0

    monkeypatch.setattr(helpers.video_manager, "get_video", get_video)
    monkeypatch.setattr(helpers, "export_info", export)
    monkeypatch.setattr(helpers.video_manager, "get_resolution", resolution)
    monkeypatch.setattr(helpers.video_manager, "is_sd", sd)


def test_process_media_disc_year_fallbacks_edit_and_low_fps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def resolution(*_args, **_kwargs):
        return "1080p"

    async def get_resolution(*_args, **_kwargs):
        return "1080p", False

    async def sd(_value):
        return 0

    async def export(*_args, **_kwargs):
        return {"media": {"track": []}}

    monkeypatch.setattr(helpers, "mi_resolution", resolution)
    monkeypatch.setattr(
        helpers.video_manager, "get_resolution", get_resolution
    )
    monkeypatch.setattr(helpers.video_manager, "is_sd", sd)
    monkeypatch.setattr(helpers, "export_info", export)
    monkeypatch.setattr(
        helpers, "guessit_fn", lambda *_args, **_kwargs: {"title": "Guessed"}
    )

    bdmv = _media_meta(tmp_path, is_disc="BDMV", resolution="")
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(name_result=("Title", "", 2024)),
            bdmv,
            str(tmp_path),
            {
                "title": "Disc-Title",
                "label": "Disc-Label",
                "video": [{"fps": "24", "res": "1080p"}],
            },
        )
    )
    assert (
        result[0] == "Title" and bdmv.hfr is False and bdmv.search_year == ""
    )

    fallback = _media_meta(tmp_path, is_disc="BDMV", resolution="1080p")
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(name_error=RuntimeError("name failed")),
            fallback,
            str(tmp_path),
            {
                "title": "Disc-Title",
                "label": "Disc-Label",
                "video": [{"fps": "bad", "res": "1080p"}],
            },
        )
    )
    assert result[0] == "Guessed" and fallback.search_year == ""

    dvd_root = tmp_path / "DVD" / "VIDEO_TS"
    dvd_root.mkdir(parents=True)
    dvd = _media_meta(
        tmp_path,
        is_disc="DVD",
        discs=[{"path": str(dvd_root), "main_set": ["01"]}],
        edit=True,
        mediainfo={"cached": True},
    )
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(), dvd, str(dvd_root.parent), {}
        )
    )
    assert result[5] == {"cached": True} and dvd.search_year == ""

    evo = tmp_path / "largest.evo"
    evo.write_bytes(b"video")
    hddvd = _media_meta(
        tmp_path,
        is_disc="HDDVD",
        discs=[{"path": str(tmp_path / "HD-DVD"), "largest_evo": str(evo)}],
        edit=True,
        mediainfo={"cached": True},
    )
    result = asyncio.run(
        helpers.process_media_files(_MediaPrep(), hddvd, str(tmp_path), {})
    )
    assert result[5] == {"cached": True} and hddvd.search_year == ""


def test_process_media_filename_fallbacks_aka_and_metadata_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "fallback.video.mkv"
    video.write_bytes(b"video")
    _patch_process_video(monkeypatch, video)

    calls = 0

    def fallback_guess(_value, _options=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("first fallback fails")
        return {"title": "Fallback AKA Extra", "year": 2024}

    monkeypatch.setattr(helpers, "guessit_fn", fallback_guess)
    meta = _media_meta(tmp_path, path=str(video), category="MOVIE", edit=False)
    result = asyncio.run(
        helpers.process_media_files(
            _MediaPrep(name_result=("", "", None)), meta, str(video), {}
        )
    )
    assert result[0].strip() == "Fallback"

    monkeypatch.setattr(
        helpers,
        "guessit_fn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("all guesses fail")
        ),
    )
    with pytest.raises(Exception, match="Error processing filename"):
        asyncio.run(
            helpers.process_media_files(
                _MediaPrep(name_result=("", "", None)),
                _media_meta(tmp_path, path=str(video), category="MOVIE"),
                str(video),
                {},
            )
        )

    class BookFailure(_MediaPrep):
        async def _gather_book_prep(self, _meta, _videopath, _base_dir):
            raise ItemProcessingError("book rejected")

    monkeypatch.setattr(
        helpers,
        "guessit_fn",
        lambda *_args, **_kwargs: {"title": "Book", "year": 2024},
    )
    book = tmp_path / "book.epub"
    book.write_bytes(b"book")
    with pytest.raises(ItemProcessingError, match="book rejected"):
        asyncio.run(
            helpers.process_media_files(
                BookFailure(),
                _media_meta(tmp_path, path=str(book), category="BOOK"),
                str(book),
                {},
            )
        )

    class GameFailure(_MediaPrep):
        async def _gather_game_prep(self, _meta, _videopath, _base_dir):
            raise RuntimeError("game rejected")

    game = tmp_path / "game.iso"
    game.write_bytes(b"game")
    with pytest.raises(RuntimeError, match="game rejected"):
        asyncio.run(
            helpers.process_media_files(
                GameFailure(),
                _media_meta(tmp_path, path=str(game), category="GAME"),
                str(game),
                {},
            )
        )


def test_detect_audiobook_fallback_audio_and_latin1_nfo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services import audio_classification_service

    audiobook = tmp_path / "audiobook.mp3"
    audiobook.write_bytes(b"audio")
    monkeypatch.setattr(
        audio_classification_service,
        "detect_audio_category",
        AsyncMock(
            return_value=SimpleNamespace(
                category="BOOK", is_audiobook=True, evidence=["narrator"]
            )
        ),
    )
    meta = Meta(path=str(audiobook))
    asyncio.run(
        helpers.detect_disc_and_category(_detection_prep(audiobook), meta)
    )
    assert meta.category == "BOOK" and meta.audiobook is True

    fallback_dir = tmp_path / "fallback-audio"
    fallback_dir.mkdir()
    (fallback_dir / "chapter.m4b").write_bytes(b"audio")
    monkeypatch.setattr(
        audio_classification_service,
        "detect_audio_category",
        AsyncMock(
            return_value=SimpleNamespace(
                category="NONE", is_audiobook=False, evidence=[]
            )
        ),
    )
    meta = Meta(path=str(fallback_dir))
    asyncio.run(
        helpers.detect_disc_and_category(_detection_prep(fallback_dir), meta)
    )
    assert meta.category == "BOOK"

    game_dir = tmp_path / "latin-game"
    game_dir.mkdir()
    nfo = game_dir / "release.nfo"
    nfo.write_bytes(b"Store: https://store.steampowered.com/app/123/game\xff")
    real_open = helpers.aiofiles.open
    attempts = 0

    def controlled_open(path, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise UnicodeError("utf8 failed")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(helpers.aiofiles, "open", controlled_open)
    meta = Meta(path=str(game_dir))
    asyncio.run(
        helpers.detect_disc_and_category(_detection_prep(game_dir), meta)
    )
    assert meta.category == "GAME" and attempts == 2


def test_search_metadata_plain_and_invalid_manual_imdb_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_search_globals(monkeypatch)
    manager = _SearchManager()
    prep = _search_prep(manager)

    plain = _search_meta(
        tmp_path, category="BOOK", imdb_id=0, imdb_manual="1234567"
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            plain,
            "Book",
            "Book",
            plain.path,
            "Book",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert plain.imdb_id == 1234567

    invalid = _search_meta(
        tmp_path, category="BOOK", imdb_id=0, imdb_manual="invalid"
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            invalid,
            "Book",
            "Book",
            invalid.path,
            "Book",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert invalid.imdb_id == 0


def test_search_metadata_empty_first_and_second_sonarr_radarr_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_search_globals(monkeypatch)

    manager = _SearchManager()
    manager.sonarr_result = None
    prep = _search_prep(manager)
    tv = _search_meta(
        tmp_path,
        category="TV",
        tvdb_id=0,
        tmdb_id=0,
        skip_trackers=False,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            tv,
            "Show",
            "Show",
            tv.path,
            "Show",
            "file",
            True,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert "sonarr" in manager.calls

    manager = _SearchManager()
    manager.radarr_result = None
    prep = _search_prep(manager)
    movie = _search_meta(
        tmp_path,
        category="MOVIE",
        tmdb_id=0,
        skip_trackers=False,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            movie,
            "Movie",
            "Movie",
            movie.path,
            "Movie",
            "file",
            False,
            True,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert "radarr" in manager.calls

    manager = _SearchManager()
    manager.sonarr_result = None
    prep = _search_prep(manager)
    tv = _search_meta(
        tmp_path,
        category="TV",
        tvdb_id=77,
        tmdb_id=0,
        skip_trackers=False,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            tv,
            "Show",
            "Show",
            tv.path,
            "Show",
            "file",
            True,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert manager.calls.count("sonarr") >= 1

    manager = _SearchManager()
    manager.radarr_result = None
    prep = _search_prep(manager)
    movie = _search_meta(
        tmp_path,
        category="MOVIE",
        tmdb_id=77,
        skip_trackers=False,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            movie,
            "Movie",
            "Movie",
            movie.path,
            "Movie",
            "file",
            False,
            True,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert manager.calls.count("radarr") >= 1


def test_search_metadata_second_sonarr_and_radarr_fill_remaining_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_search_globals(monkeypatch)
    manager = _SearchManager()
    manager.sonarr_result = {
        "tvdb_id": 77,
        "imdb_id": 88,
        "tvmaze_id": 99,
        "tmdb_id": 111,
        "genres": ["Drama"],
        "year": 2024,
        "release_group": "GROUP",
    }
    prep = _search_prep(manager)
    tv = _search_meta(
        tmp_path,
        category="TV",
        tvdb_id=77,
        imdb_id=0,
        tvmaze_id=0,
        tmdb_id=0,
        skip_trackers=False,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            tv,
            "Show",
            "Show",
            tv.path,
            "Show",
            "file",
            True,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert (tv.imdb_id, tv.tvmaze_id, tv.tmdb_id) == (88, 99, 111)

    manager = _SearchManager()
    manager.radarr_result = {
        "imdb_id": 88,
        "tmdb_id": 77,
        "genres": ["Drama"],
        "year": 2024,
        "release_group": "GROUP",
    }
    prep = _search_prep(manager)
    movie = _search_meta(
        tmp_path,
        category="MOVIE",
        tmdb_id=77,
        imdb_id=0,
        skip_trackers=False,
        matched_tracker=False,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            movie,
            "Movie",
            "Movie",
            movie.path,
            "Movie",
            "file",
            False,
            True,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert movie.imdb_id == 88


def test_search_metadata_full_imdb_failure_and_second_tmdb_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _SearchManager()
    prep = _search_prep(manager)
    monkeypatch.setattr(
        helpers.video_manager, "get_type", AsyncMock(return_value="WEBDL")
    )
    monkeypatch.setattr(
        helpers.video_manager,
        "get_video_duration",
        AsyncMock(return_value=120),
    )

    async def fail_full_search(*_args, quickie: bool = False, **_kwargs):
        if quickie:
            return 0
        raise RuntimeError("full imdb failed")

    monkeypatch.setattr(helpers.imdb_manager, "search_imdb", fail_full_search)
    meta = _search_meta(
        tmp_path, category="MOVIE", tmdb_id=123, imdb_id=0, skip_trackers=True
    )
    with pytest.raises(Exception, match="Error searching IMDb"):
        asyncio.run(
            helpers.search_metadata(
                prep,
                meta,
                "Movie",
                "Movie",
                meta.path,
                "Movie",
                "file",
                False,
                False,
                False,
                _SearchClient(),
                {},
                {},
            )
        )

    class SequentialManager(_SearchManager):
        def __init__(self) -> None:
            super().__init__()
            self.responses = iter(
                (("MOVIE", 0, "", False), ("MOVIE", 321, "es", True))
            )

        async def get_tmdb_from_imdb(self, *_args, **_kwargs):
            self.calls.append("tmdb_from_imdb")
            return next(self.responses)

    manager = SequentialManager()
    prep = _search_prep(manager)
    monkeypatch.setattr(
        helpers.imdb_manager, "search_imdb", AsyncMock(return_value=1234567)
    )
    meta = _search_meta(
        tmp_path,
        category="MOVIE",
        imdb_id=1234567,
        tmdb_id=0,
        skip_trackers=True,
    )
    asyncio.run(
        helpers.search_metadata(
            prep,
            meta,
            "Movie",
            "Movie",
            meta.path,
            "Movie",
            "file",
            False,
            False,
            False,
            _SearchClient(),
            {},
            {},
        )
    )
    assert manager.calls.count("tmdb_from_imdb") == 2
    assert (
        meta.tmdb_id == 321
        and meta.original_language == "es"
        and meta.no_ids is True
    )
