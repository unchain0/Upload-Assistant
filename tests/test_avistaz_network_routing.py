from types import SimpleNamespace

import pytest

from src.integrations.trackers.AVISTAZ.routing import AvistaZNetworkRouter
from src.services.tracker_status_service import merge_tracker_status


class FakeTracker:
    cookie_valid = True

    def __init__(self, config):
        self.config = config

    async def validate_credentials(self, _meta):
        return self.cookie_valid


def make_meta(**overrides):
    values = {
        "origin_country": ["US"],
        "year": 2020,
        "sd": False,
        "resolution": "1080p",
        "trackers": ["PRIVATEHD"],
        "tracker_status": {},
        "unattended": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def router(auto_redirect=True):
    return AvistaZNetworkRouter({"DEFAULT": {"avistaz_network_auto_redirect": auto_redirect}}, {"AVISTAZ": FakeTracker, "CINEMAZ": FakeTracker, "PRIVATEHD": FakeTracker})


@pytest.mark.asyncio
async def test_old_privatehd_content_is_redirected_after_cookie_validation():
    meta = make_meta(year=1970)

    await router().apply(meta)

    assert meta.trackers == ["CINEMAZ"]
    assert meta.tracker_status["PRIVATEHD"]["redirected_to"] == "CINEMAZ"
    assert meta.tracker_status["CINEMAZ"]["redirected_from"] == ["PRIVATEHD"]


@pytest.mark.asyncio
async def test_redirect_keeps_source_when_destination_cookie_is_invalid():
    meta = make_meta(year=1970)
    FakeTracker.cookie_valid = False
    try:
        await router().apply(meta)
    finally:
        FakeTracker.cookie_valid = True

    assert meta.trackers == ["PRIVATEHD"]
    assert "routing_error" in meta.tracker_status["PRIVATEHD"]


@pytest.mark.asyncio
async def test_conflicting_rules_require_manual_review():
    meta = make_meta(year=1970, origin_country=["JP"])

    await router().apply(meta)

    assert meta.trackers == ["PRIVATEHD"]
    assert meta.tracker_status["PRIVATEHD"]["routing_suggested_to"] is None


@pytest.mark.asyncio
async def test_asian_privatehd_content_is_redirected_to_avistaz():
    meta = make_meta(origin_country=["JP"])

    await router().apply(meta)

    assert meta.trackers == ["AVISTAZ"]


@pytest.mark.asyncio
async def test_disabled_string_value_does_not_enable_unattended_redirects():
    meta = make_meta(year=1970)

    await router("false").apply(meta)

    assert meta.trackers == ["PRIVATEHD"]
    assert meta.tracker_status["PRIVATEHD"]["routing_suggested_to"] == "CINEMAZ"


@pytest.mark.asyncio
async def test_recent_english_content_on_cinemaz_is_only_suggested():
    meta = make_meta(trackers=["CINEMAZ"])

    await router().apply(meta)

    assert meta.trackers == ["CINEMAZ"]
    assert meta.tracker_status["CINEMAZ"]["routing_suggested_to"] == "PRIVATEHD"


@pytest.mark.asyncio
async def test_sd_resolution_prevents_cinemaz_to_privatehd_suggestion():
    meta = make_meta(trackers=["CINEMAZ"], resolution="480p", sd=False)

    await router().apply(meta)

    assert meta.tracker_status == {}


def test_merge_tracker_status_preserves_routing_metadata():
    merged = merge_tracker_status(
        {"CINEMAZ": {"upload": True, "skipped": False}},
        {"PRIVATEHD": {"redirected_to": "CINEMAZ", "skipped": True}, "CINEMAZ": {"redirected_from": ["PRIVATEHD"]}},
    )

    assert merged["PRIVATEHD"]["redirected_to"] == "CINEMAZ"
    assert merged["CINEMAZ"] == {"redirected_from": ["PRIVATEHD"], "upload": True, "skipped": False}


def test_router_handles_invalid_year_and_explicit_sd():
    current = router()
    assert current._is_older_than_50_years(make_meta(year="bad")) is False
    assert current._is_sd(make_meta(sd=True, resolution="1080p")) is True


def test_privatehd_direct_region_decisions_cover_cinemaz_and_avistaz():
    current = router()
    cinema = current.decide("PRIVATEHD", make_meta(origin_country=["FR"], year=2020))
    avista = current.decide("PRIVATEHD", make_meta(origin_country=["JP"], year=2020))
    assert cinema is not None and cinema.destination == "CINEMAZ"
    assert avista is not None and avista.destination == "AVISTAZ"


def test_avistaz_direct_region_decisions_cover_privatehd_and_cinemaz():
    current = router()
    privatehd = current.decide("AVISTAZ", make_meta(origin_country=["US"], trackers=["AVISTAZ"]))
    cinema = current.decide("AVISTAZ", make_meta(origin_country=["FR"], trackers=["AVISTAZ"]))
    assert privatehd is not None and privatehd.destination == "PRIVATEHD"
    assert cinema is not None and cinema.destination == "CINEMAZ"


@pytest.mark.asyncio
async def test_non_network_tracker_is_ignored():
    meta = make_meta(trackers=["OTHER"])
    await router().apply(meta)
    assert meta.trackers == ["OTHER"]
    assert meta.tracker_status == {}


@pytest.mark.asyncio
async def test_attended_user_can_decline_redirect(monkeypatch):
    meta = make_meta(year=1970, unattended=False)
    monkeypatch.setattr("src.integrations.trackers.AVISTAZ.routing.cli_ui.ask_yes_no", lambda *_args, **_kwargs: False)

    await router().apply(meta)

    assert meta.trackers == ["PRIVATEHD"]
    assert meta.tracker_status["PRIVATEHD"]["routing_suggested_to"] == "CINEMAZ"


@pytest.mark.asyncio
async def test_missing_destination_class_records_routing_error():
    current = AvistaZNetworkRouter({"DEFAULT": {"avistaz_network_auto_redirect": True}}, {"PRIVATEHD": FakeTracker})
    meta = make_meta(year=1970)

    await current.apply(meta)

    assert meta.trackers == ["PRIVATEHD"]
    assert "not available" in meta.tracker_status["PRIVATEHD"]["routing_error"]


@pytest.mark.asyncio
async def test_destination_validation_exception_records_routing_error():
    class BrokenTracker(FakeTracker):
        async def validate_credentials(self, _meta):
            raise RuntimeError("broken credentials")

    current = AvistaZNetworkRouter(
        {"DEFAULT": {"avistaz_network_auto_redirect": True}},
        {"PRIVATEHD": FakeTracker, "CINEMAZ": BrokenTracker, "AVISTAZ": FakeTracker},
    )
    meta = make_meta(year=1970)

    await current.apply(meta)

    assert meta.trackers == ["PRIVATEHD"]
    assert "broken credentials" in meta.tracker_status["PRIVATEHD"]["routing_error"]


def test_cinemaz_asian_production_routes_to_avistaz():
    decision = router().decide("CINEMAZ", make_meta(origin_country=["JP"], trackers=["CINEMAZ"]))
    assert decision is not None and decision.destination == "AVISTAZ"
