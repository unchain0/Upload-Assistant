import pytest

from src.integrations.trackers.announce_url import required_announce_url


def test_required_announce_url_accepts_string() -> None:
    assert (
        required_announce_url("https://tracker.invalid/announce", "TEST")
        == "https://tracker.invalid/announce"
    )


def test_required_announce_url_accepts_nonempty_string_list() -> None:
    value = ["https://one.invalid/announce", "https://two.invalid/announce"]
    assert required_announce_url(value, "TEST") == value


@pytest.mark.parametrize(
    "value", [None, [], ["https://tracker.invalid/announce", 123]]
)
def test_required_announce_url_rejects_malformed_values(value: object) -> None:
    with pytest.raises(
        ValueError, match="TEST: announce URL is not configured"
    ):
        required_announce_url(value, "TEST")
