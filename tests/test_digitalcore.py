from src.integrations.image_hosts.rehosting import _image_host
from src.integrations.trackers.digitalcore import DigitalCore


def test_uses_the_image_hosts_approved_by_digitalcore():
    assert "ptscreens" in DigitalCore.approved_image_hosts
    assert "onlyimage" not in DigitalCore.approved_image_hosts
    assert (
        _image_host(
            "https://img2.ptscreens.com/image.png",
            DigitalCore.image_host_policy.url_host_mapping,
        )
        == "ptscreens"
    )
