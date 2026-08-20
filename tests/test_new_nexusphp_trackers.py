import asyncio

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.lemonhd import LemonHD
from src.integrations.trackers.NEXUSPHP.longpt import LongPT
from src.integrations.trackers.NEXUSPHP.oneptba import OnePTBA
from src.integrations.trackers.NEXUSPHP.ptfans import PTFans
from src.integrations.trackers.NEXUSPHP.ptgtk import PTGTK
from src.integrations.trackers.NEXUSPHP.ptzone import PTZone
from src.integrations.trackers.NEXUSPHP.xingyungept import XingyungePT

dummy_config = {"DEFAULT": {"tmdb_api": "dummy_key"}, "TRACKERS": {}}


def test_lemonhd_methods():
    tracker = LemonHD(dummy_config)
    assert tracker.tracker == "LEMONHD"
    assert tracker.base_url == "https://lemonhd.net"

    meta_movie = Meta(category="MOVIE", type="ENCODE", resolution="1080p", video_codec="H.264", audio="DTS")
    assert tracker.get_category(meta_movie) == 401
    assert tracker.get_type(meta_movie) == 2
    assert tracker.get_codec(meta_movie) == 1
    assert tracker.get_resolution(meta_movie) == 2
    assert tracker.get_audio_codec(meta_movie) == 5

    assert tracker.get_type(Meta(category="TV", type="HDTV")) == 5
    assert tracker.get_douban_url(Meta(douban_id=12345)) == "https://movie.douban.com/subject/12345/"
    assert tracker.get_imdb_url(Meta(imdb_id="tt1234567", imdb_info={"imdb_url": "https://imdb.com/title/tt1234567"})) == ""

    meta_tv = Meta(category="TV", genres=["Documentary"])
    assert tracker.get_category(meta_tv) == 405


def test_lemonhd_data_getters_return_single_field_dictionaries():
    tracker = LemonHD(dummy_config)
    meta = Meta(category="MOVIE", type="ENCODE", resolution="1080p", video_codec="H.264", audio="DTS")

    assert asyncio.run(tracker.get_name(meta)) == {"name": meta.name}
    assert asyncio.run(tracker.get_category_data(meta)) == {"type": 401}
    assert asyncio.run(tracker.get_type_data(meta)) == {"medium_sel[4]": 2}
    assert asyncio.run(tracker.get_codec_data(meta)) == {"codec_sel[4]": 1}
    assert asyncio.run(tracker.get_resolution_data(meta)) == {"standard_sel[4]": 2}
    assert asyncio.run(tracker.get_audio_codec_data(meta)) == {"audiocodec_sel[4]": 5}
    assert asyncio.run(tracker.get_group_tag_data(meta)) == {"team_sel[4]": 5}
    assert asyncio.run(tracker.get_checkboxes_data(meta)) == {}
    assert asyncio.run(tracker.get_anonymous_data(meta)) == {}
    assert asyncio.run(tracker.get_imdb_data(meta)) == {}
    assert asyncio.run(tracker.get_douban_data(meta)) == {}
    assert asyncio.run(tracker.get_region_data(meta)) == {}
    assert asyncio.run(tracker.get_container_data(meta)) == {}


def test_oneptba_methods():
    tracker = OnePTBA(dummy_config)
    assert tracker.tracker == "1PTBA"
    assert tracker.base_url == "https://1ptba.com"

    meta_movie = Meta(category="MOVIE", type="ENCODE", resolution="2160p", video_codec="HEVC", audio="FLAC", hdr="HDR10")
    assert tracker.get_category(meta_movie) == 401
    assert tracker.get_type(meta_movie) == 7
    assert tracker.get_codec(meta_movie) == 18
    assert tracker.get_resolution(meta_movie) == 16
    assert tracker.get_audio_codec(meta_movie) == 1
    assert tracker.get_region(meta_movie) == 22
    assert "7" in tracker.get_checkboxes(meta_movie)
    assert tracker.get_type(Meta(category="MOVIE", type="WEB-DL")) == 7
    assert tracker.get_region(Meta(category="MOVIE", type="WEB-DL")) == 23
    assert tracker.get_type(Meta(category="TV", type="HDTV")) == 5


def test_xingyungept_methods():
    tracker = XingyungePT(dummy_config)
    assert tracker.tracker == "XINGYUNGEPT"
    assert tracker.base_url == "https://pt.xingyungept.org"

    meta_tv = Meta(category="TV", type="WEB-DL", resolution="1080p", video_codec="H.264", audio="AAC", tv_pack=True)
    assert tracker.get_category(meta_tv) == 402
    assert tracker.get_type(meta_tv) == 4
    assert tracker.get_codec(meta_tv) == 1
    assert tracker.get_resolution(meta_tv) == 3
    assert tracker.get_audio_codec(meta_tv) == 14
    assert "11" in tracker.get_checkboxes(meta_tv)
    assert tracker.get_type(Meta(category="TV", type="HDTV")) == 5
    assert tracker.get_douban_url(Meta(douban_id=12345)) == "https://movie.douban.com/subject/12345/"
    assert tracker.get_imdb_url(Meta(imdb_id="tt1234567", imdb_info={"imdb_url": "https://imdb.com/title/tt1234567"})) == ""


def test_ptzone_methods():
    tracker = PTZone(dummy_config)
    assert tracker.tracker == "PTZONE"
    assert tracker.base_url == "https://ptzone.xyz"

    meta_movie = Meta(category="MOVIE", is_disc="BDMV", resolution="2160p", video_codec="HEVC", audio="TrueHD")
    assert tracker.get_category(meta_movie) == 401
    assert tracker.get_type(meta_movie) == 10
    assert tracker.get_codec(meta_movie) == 6
    assert tracker.get_resolution(meta_movie) == 6
    assert tracker.get_audio_codec(meta_movie) == 14
    assert tracker.get_type(Meta(category="TV", type="HDTV")) == 5
    assert tracker.get_douban_url(Meta(douban_id=12345)) == "https://movie.douban.com/subject/12345/"
    assert tracker.get_imdb_url(Meta(imdb_id="tt1234567", imdb_info={"imdb_url": "https://imdb.com/title/tt1234567"})) == ""


def test_nexusphp_category_documentary_and_tv_show_branches():
    cases = (
        (LemonHD, 405, 407),
        (PTGTK, 404, 403),
        (PTZone, 404, 403),
        (LongPT, 404, 403),
        (XingyungePT, 404, 403),
        (OnePTBA, 404, 403),
        (PTFans, 406, 405),
    )
    documentary = Meta(category="TV", genres=["Documentary"], keywords=[])
    tv_show = Meta(category="TV", genres=["game show"], keywords=[])
    for tracker_class, documentary_id, tv_show_id in cases:
        tracker = tracker_class(dummy_config)
        assert tracker.get_category(documentary) == documentary_id
        assert tracker.get_category(tv_show) == tv_show_id


def test_nexusphp_remaining_codec_and_disc_mapping_branches():
    assert LemonHD(dummy_config).get_audio_codec(Meta(audio="TrueHD Atmos")) == 1
    assert LongPT(dummy_config).get_audio_codec(Meta(audio="DDP 5.1")) == 10
    assert XingyungePT(dummy_config).get_audio_codec(Meta(audio="TrueHD Atmos")) == 12

    diy_uhd = Meta(category="MOVIE", is_disc="BDMV", resolution="2160p", diy_disc=True)
    oneptba = OnePTBA(dummy_config)
    assert oneptba.get_type(diy_uhd) == 17
    assert oneptba.get_region(diy_uhd) == 17

    ptfans = PTFans(dummy_config)
    assert ptfans.get_codec(Meta(video_codec="H264", source="bluray")) == 4
    assert ptfans.get_codec(Meta(video_codec="VC-1", source="bluray")) == 3


def test_nexusphp_remaining_small_mapping_branches():
    longpt = LongPT(dummy_config)
    assert longpt.get_type(Meta(type="REMUX", resolution="2160p")) == 11
    assert longpt.get_douban_url(Meta()) == ""

    assert OnePTBA(dummy_config).get_douban_url(Meta()) == ""
