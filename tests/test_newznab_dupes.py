from src.integrations.trackers.USENET.search_helpers import parse_newznab_dupes

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Fictional.Movie.2020.1080p-FICTIONAL</title><guid>0123456789abcdef0123456789abcdef</guid></item>
<item><title>Fictional.Show.S01-FICTIONAL</title><guid>fedcba9876543210fedcba9876543210</guid>
<link>https://fictional.example/getnzb/fedcba9876543210fedcba9876543210.nzb</link></item>
</channel></rss>"""


def test_generated_release_link_exposes_guid_as_public_id() -> None:
    results = parse_newznab_dupes(FEED, "https://fictional.example/releases/")

    assert results[0]["link"] == (
        "https://fictional.example/releases/0123456789abcdef0123456789abcdef"
    )
    assert results[0]["id"] == "0123456789abcdef0123456789abcdef"
    assert results[1]["link"] == (
        "https://fictional.example/getnzb/fedcba9876543210fedcba9876543210.nzb"
    )
    assert results[1]["id"] is None
