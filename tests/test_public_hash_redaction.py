from src.webui_events import plain_text


def test_public_release_hash_survives_redaction_only_when_explicit() -> None:
    public_hash = "0123456789abcdef0123456789abcdef"
    link = f"https://tracker.invalid/torrents/{public_hash}"

    assert plain_text(link, public_ids=(public_hash,)) == link
    assert "[REDACTED]" in plain_text(link)
    assert "[REDACTED]" in plain_text(
        f"https://tracker.invalid/torrents/{'f' * 32}",
        public_ids=(public_hash,),
    )
