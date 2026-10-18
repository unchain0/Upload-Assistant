"""CLI ports from upstream development, exercised without live trackers."""

import asyncio
from pathlib import Path

import httpx
import pytest

from src.delivery.cli.arguments import Args
from src.domain_models.image_host import IMAGE_HOST_CONFIG_KEYS
from src.domain_models.release import Meta
from src.domain_models.release_identity import client_export_infohash
from src.domain_models.tracker_catalog import KNOWN_TRACKERS, TRACKER_DEFINITIONS
from src.engines.region_mapping import get_mediainfo_service, get_region
from src.integrations.external_apis.btn import BtnIdManager
from src.integrations.external_apis.tvdb import _best_series_result
from src.integrations.filesystem.cleanup import _shutdown_thread_executor
from src.integrations.image_hosts.catbox import CATBOX_UPLOAD_URL
from src.integrations.image_hosts.contracts import IMAGE_HOST_SPECS
from src.integrations.image_hosts.uploader import (
    _image_upload_handler,
    _upload_catbox_host,
)
from src.integrations.media.scene_detection import SceneManager
from src.integrations.media.video import VideoManager
from src.integrations.torrent.torrent_creator import (
    TorrentCreator,
    _TorrentCreationPlan,
)
from src.integrations.torrent_clients import bandwidth, qbittorrent
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk
from src.integrations.trackers.UNIT3D.seedpool import Seedpool
from src.integrations.trackers.USENET.nzbnest import NzbNest
from src.integrations.trackers.bjshare import BJShare
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.hdbits import HDBits
from src.integrations.trackers.NEXUSPHP.qingwa import QingWa
from src.integrations.trackers.registry import tracker_class_map
from src.integrations.trackers.totheglory import ToTheGlory
from src.services.configuration_validation_service import DEFAULT_KEY_TYPES
from src.services.edition_identity_service import get_edition
from src.services.preparation_helpers import should_lookup_torrent_properties
from src.services.release_naming_service import NameManager


def _config(tracker: str) -> dict[str, dict[str, object]]:
    return {
        "DEFAULT": {"tmdb_api": "test-tmdb-key", "screens": 4},
        "TRACKERS": {tracker: {}},
    }


def test_repacks_two_through_nine_and_version_ten() -> None:
    repack = asyncio.run(
        get_edition("Movie.REPACK9.1080p", None, [], "", Meta(category="MOVIE"))
    )
    version = asyncio.run(
        get_edition("Movie.V10.1080p", None, [], "", Meta(category="MOVIE"))
    )
    packaged = asyncio.run(
        get_edition("Movie.REPACKAGED.1080p", None, [], "", Meta(category="MOVIE"))
    )

    assert repack[1] == "REPACK9"
    assert version[1] == "REPACK9"
    assert packaged[1] == ""


def test_uhd_token_ignores_the_release_group() -> None:
    video = VideoManager()

    marked = asyncio.run(video.get_uhd("ENCODE", {}, "1080p", "Movie.UHD.mkv"))
    grouped = asyncio.run(
        video.get_uhd("ENCODE", {}, "1080p", "Movie.GROUPUHD.mkv")
    )
    source = asyncio.run(
        video.get_uhd(
            "ENCODE",
            {"Source": "Blu-ray", "Other": "Ultra HD"},
            "1080p",
            "plain.mkv",
        )
    )

    assert marked == "UHD"
    assert grouped == ""
    assert source == "UHD"


def test_book_source_strips_before_comparing() -> None:
    meta = Meta(category="BOOK", source="  RETAIL  ", type="EPUB")

    assert NameManager._book_source(meta) == "RETAiL"


def test_reused_infohash_and_qbittorrent_https_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    infohash = "a" * 40
    captured: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.append(kwargs)

        def app_version(self) -> str:
            return "1"

        def auth_log_in(self) -> None:
            return None

    monkeypatch.setattr(qbittorrent.qbittorrentapi, "Client", FakeClient)
    monkeypatch.setattr(bandwidth.qbittorrentapi, "Client", FakeClient)
    qbittorrent.QbittorrentClientMixin._new_qbit_client(
        {"qbit_url": "https://qb.example", "qbit_port": 443, "qbit_api_key": "k"}
    )
    bandwidth.Wait._api_key_client({}, "https://qb.example", 443, True)
    bandwidth.Wait._password_client(
        {"qbit_user": "u", "qbit_pass": "p"}, "https://qb.example", 443, True
    )
    meta = Meta(reuse_torrent_path="/tmp/reuse.torrent", infohash=infohash)

    assert client_export_infohash(f"/tmp/{infohash}.torrent", "parsed") == infohash
    assert should_lookup_torrent_properties(meta, None) is False
    assert [item["FORCE_SCHEME_FROM_HOST"] for item in captured] == [True, True, True]


def test_empty_unit3d_description_is_dropped() -> None:
    common = Common({"DEFAULT": {}, "TRACKERS": {}})

    def clean(
        _description: str, _url: str
    ) -> tuple[str, list[dict[str, str]]]:
        return "", [{"img_url": "https://img.example/a.png"}]

    common._clean_unit3d_description = clean  # type: ignore[method-assign]
    images = [{"img_url": "https://img.example/a.png"}]
    attended = common._unit3d_description_result(
        Meta(unattended=False), "SEEDPOOL", "https://tracker", "<p></p>", False
    )
    unattended = common._unit3d_description_result(
        Meta(unattended=True), "SEEDPOOL", "https://tracker", "<p></p>", False
    )

    assert attended == ("", [])
    assert unattended == ("", images)


def test_seedpool_keeps_spaces_in_the_original_filename() -> None:
    meta = Meta(category="MOVIE", filelist=["/tmp/Scene File.mkv"])

    name = asyncio.run(Seedpool(_config("SEEDPOOL")).get_name(meta))

    assert name == {"name": "Scene File"}


def test_tvdb_apostrophe_prefers_the_matching_series() -> None:
    results = [
        {"id": 1, "name": "Wrong Show", "year": "2019"},
        {"id": 2, "name": "Marvel's Agents", "year": "2019"},
    ]

    chosen = _best_series_result(results, "2019", "Marvels Agents")

    assert chosen["id"] == 2


def test_region_and_distributor_reach_release_metadata() -> None:
    meta, _parser, _before = Args(_config("UNUSED")).parse(
        ["/tmp/movie.mkv", "--region", "usa", "--distributor", "Criterion"],
        Meta(),
    )

    assert meta.region == "usa"
    assert meta.distributor == "Criterion"


def test_screenshot_header_and_recovered_screenshot_files(tmp_path: Path) -> None:
    root = tmp_path / "screens"
    root.mkdir()
    (root / "shot.png").write_bytes(b"png")
    (root / "POSTER.png").write_bytes(b"png")
    builder = DescriptionBuilder(
        "SEEDPOOL",
        {
            "DEFAULT": {"screenshot_header": "[center]Shots[/center]"},
            "TRACKERS": {"SEEDPOOL": {}},
        },
    )

    header = asyncio.run(builder.screenshot_header(Meta()))
    visible = HDBits._visible_screenshot_files(root)

    assert header == "[center]Shots[/center]"
    assert visible == [str(root / "shot.png")]


def test_external_subtitles_inside_the_torrent_root_are_included(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir()
    inside = root / "movie.en.srt"
    outside = tmp_path / "other.srt"
    inside.write_text("1\n", encoding="utf-8")
    outside.write_text("1\n", encoding="utf-8")
    meta = Meta(
        category="MOVIE",
        subtitle_files=[str(inside), str(outside)],
    )
    plan = _TorrentCreationPlan(
        path=root, creation_filelist=[], include=[], exclude=[], is_subs=True
    )

    selected = TorrentCreator._selected_subtitle_paths(
        root.resolve(), [str(inside), str(outside)]
    )
    included = TorrentCreator._custom_include(meta, plan)

    assert selected == ["movie.en.srt"]
    assert "movie.en.srt" in included


def test_mediainfo_amazon_signature_sets_the_streaming_service() -> None:
    meta = Meta(
        category="MOVIE",
        resolution="1080p",
        type="WEBDL",
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "Video",
                        "BitRate_Mode": "CBR",
                        "BitRate_Nominal": "10000000",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Channels": "6.0",
                        "BitRate": "640000",
                    },
                ]
            }
        },
    )
    plain = Meta(category="MOVIE", resolution="720p", type="WEBDL")

    service = asyncio.run(get_mediainfo_service(meta))
    skipped = asyncio.run(get_mediainfo_service(plain))

    assert service[0] != ""
    assert skipped == ("", "")


def test_nzbnest_and_qingwa_are_cli_trackers() -> None:
    nest = NzbNest(_config("NZBNEST"))
    qingwa = QingWa(_config("QINGWA"))
    meta = Meta(
        category="TV",
        title="Show",
        year=2020,
        season="1",
        episode="1",
        type="WEBDL",
        service="AMZN",
        video_codec="HEVC",
        resolution="1080p",
        audio="DD+ 5.1",
        tag="-frog",
        tvdb_id=42,
        season_int=1,
        episode_int=2,
    )

    params = nest._search_params(meta)
    name = asyncio.run(qingwa.get_name(meta))

    assert "NZBNEST" in KNOWN_TRACKERS
    assert "QINGWA" in KNOWN_TRACKERS
    assert tracker_class_map["NZBNEST"] is NzbNest
    assert tracker_class_map["QINGWA"] is QingWa
    assert TRACKER_DEFINITIONS["NZBNEST"].is_usenet is True
    assert params["tvdbid"] == "42"
    assert params["season"] == "1"
    assert "S01E01" in name["name"]
    assert "WEB-DL" in name["name"]
    assert "H.265" in name["name"]
    assert name["name"].endswith("-frog")


def test_qingwa_codec_audio_and_category_ids() -> None:
    meta = Meta(
        category="TV",
        genres=["documentary"],
        video_codec="H.265",
        audio="DTS-HD MA",
        hdr="HDR10+",
        resolution="1080p",
    )
    tracker = QingWa(_config("QINGWA"))

    assert tracker.get_category(meta) == 404
    assert tracker.get_codec(meta) == 6
    assert tracker.get_audio_codec(meta) == 10
    assert tracker.get_resolution(meta) == 1
    assert tracker.get_checkboxes(meta) == ["13", "7"]


def test_image_hosts_include_catbox_passtheimage_lostimg_and_sharex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = "https://files.catbox.moe/abc.png"

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            data: dict[str, str] | None = None,
            files: dict[str, object] | None = None,
            timeout: float | None = None,
        ) -> FakeResponse:
            calls.append(
                {"url": url, "data": data or {}, "field": next(iter(files or {}))}
            )
            return FakeResponse()

    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = asyncio.run(
        _upload_catbox_host(str(image), {"DEFAULT": {}}, Meta(), 5.0)
    )

    assert _image_upload_handler("passtheimage") is not None
    assert IMAGE_HOST_SPECS["lostimg"].upload_url == "https://lostimg.cc/api/v1/images"
    assert _image_upload_handler("sharex") is not None
    assert calls[0]["url"] == CATBOX_UPLOAD_URL
    assert calls[0]["field"] == "fileToUpload"
    assert calls[0]["data"] == {"reqtype": "fileupload"}
    assert result["img_url"] == "https://files.catbox.moe/abc.png"


def test_manual_multi_hhd_audiobook_region_and_config_defaults() -> None:
    game = Meta(category="GAME", manual_multi=True, languages=["French"])
    book = Meta(category="BOOK", audiobook=True)

    assert NameManager._game_language_tag(game) == "MULTI"
    assert HomieHelpDesk._book_category(book) == "AUDIOBOOK"
    assert asyncio.run(get_region({"label": "Movie BRA 1080p"})) == "BRA"
    assert DEFAULT_KEY_TYPES["catbox_userhash"] == (str,)
    assert IMAGE_HOST_CONFIG_KEYS["catbox"] == ("catbox_userhash",)


def test_btn_ids_srrdb_nfo_and_hdbits_miniseries_name() -> None:
    btn = BtnIdManager._btn_ids(
        {
            "result": {
                "torrents": {
                    "1": {"ImdbID": "123", "TvdbID": "456", "Series": "Show"}
                }
            }
        }
    )
    nfo = SceneManager._details_nfo_name(
        {"files": [{"name": "Release.nfo"}, {"name": "other.rar"}]},
        "fallback",
    )
    meta = Meta(
        category="TV",
        tv_pack=True,
        title="Show, What?",
        year=2020,
        name="2020 S01 Show, What?",
        imdb_info={
            "type": "tvMiniSeries",
            "year": "2019",
            "title": "Show, What?",
            "aka": "",
        },
    )

    name = asyncio.run(HDBits(_config("HDBITS")).get_name(meta))

    assert btn == (123, 456)
    assert nfo == "Release"
    assert name == "2019 Show, What?"


def test_bjshare_skips_incompatible_audio() -> None:
    class Response:
        text = (
            '<table class="torrent_table"><tr>'
            '<td class="audio_header">Torrents Legendados</td>'
            "</tr></table>"
        )

    tracker = BJShare(_config("BJSHARE"))

    async def upload_audio(_meta: Meta) -> str:
        return "Dublado"

    tracker.get_audio = upload_audio  # type: ignore[method-assign]
    meta = Meta(category="MOVIE")

    blocked = asyncio.run(tracker._incompatible_audio_blocks(meta, Response()))

    assert blocked is True
    assert meta.skipping == "BJSHARE"


def test_system_exit_codes_stay_available_to_the_cli() -> None:
    from upload import _process_exit_code

    assert _process_exit_code(SystemExit(0)) == 0
    assert _process_exit_code(SystemExit(2)) == 2
    assert _process_exit_code(KeyboardInterrupt()) == 130


def test_ttg_cookie_export_and_executor_shutdown(tmp_path: Path) -> None:
    cookie = tmp_path / "TOTHEGLORY.json"
    cookie.write_text('{"pass": {"value": "exported-token"}}', encoding="utf-8")
    tracker = ToTheGlory({"DEFAULT": {}, "TRACKERS": {"TOTHEGLORY": {}}})
    import src.integrations.filesystem.cleanup as cleanup

    class Recorder:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def shutdown(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    recorder = Recorder()
    cleanup.thread_executor = recorder  # type: ignore[assignment]

    values = tracker._cookie_values(cookie)
    _shutdown_thread_executor()

    assert values == {"pass": "exported-token"}
    assert recorder.kwargs == {"wait": True}
    assert cleanup.thread_executor is None
