from src.integrations.image_hosts.rehosting import _image_host
from src.integrations.trackers.digitalcore import DigitalCore


def test_uses_only_renderer_safe_image_hosts_for_digitalcore():
    assert DigitalCore.approved_image_hosts == ("sharex", "ptscreens")
    assert DigitalCore.image_host_policy.preferred_image_host == "sharex"
    assert (
        _image_host(
            "https://img.digitalcore.club/image.png",
            DigitalCore.image_host_policy.url_host_mapping,
        )
        == "sharex"
    )
    assert (
        _image_host(
            "https://img2.ptscreens.com/image.png",
            DigitalCore.image_host_policy.url_host_mapping,
        )
        == "ptscreens"
    )
    assert (
        _image_host(
            "https://i.ibb.co/image.png",
            DigitalCore.image_host_policy.url_host_mapping,
        )
        not in DigitalCore.approved_image_hosts
    )


def test_ptscreens_raw_urls_use_digitalcore_csp_compatible_cdn() -> None:
    legacy = "https://img.ptscreens.com/example.png"
    expected = "https://img2.ptscreens.com/example.png"

    assert DigitalCore._normalize_renderer_image_urls(legacy) == expected
    assert DigitalCore._safe_image_url(legacy) == expected
    assert (
        DigitalCore._safe_image_url("https://img2.ptscreens.com/example.png")
        == expected
    )
