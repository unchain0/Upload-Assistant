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
