import asyncio

import upload


def test_update_notification_reuses_successful_check_during_cooldown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("upload.STATE_DIR", tmp_path)
    monkeypatch.setattr("upload.CODE_DIR", tmp_path)
    monkeypatch.setattr("upload.get_local_version", lambda _path: "v1.0")
    monkeypatch.setattr(
        "upload.get_remote_version",
        lambda _url: ("v2.0", '__version__ = "v2.0"'),
    )
    monkeypatch.setattr(
        "upload.config",
        {
            "DEFAULT": {
                "update_notification": True,
                "update_notification_cache_hours": 4,
            }
        },
    )

    assert asyncio.run(upload.update_notification()) == "v1.0"

    def fail_if_called(_url: str) -> tuple[str, str]:
        raise AssertionError("The remote version check should use the cooldown cache")

    monkeypatch.setattr("upload.get_remote_version", fail_if_called)
    assert asyncio.run(upload.update_notification()) == "v1.0"


def test_update_notification_cache_expires_after_configured_interval(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("upload.STATE_DIR", tmp_path)
    monkeypatch.setattr(
        "upload.time",
        type("Clock", (), {"time": staticmethod(lambda: 14_401)})(),
    )
    upload._update_notification_cache_path().write_text(
        '{"checked_at": 0, "remote_version": "v2.0", "remote_content": "content"}',
        encoding="utf-8",
    )

    assert upload._read_update_notification_cache(4) is None


def test_get_local_version_uses_packaged_metadata_when_file_is_absent(tmp_path, caplog) -> None:
    missing = tmp_path / "missing-version.py"

    assert upload.get_local_version(missing) == upload.application_version.__version__
    assert "Version file not found" not in caplog.text


def test_get_local_version_uses_packaged_metadata_when_file_has_no_version(
    tmp_path,
) -> None:
    version_file = tmp_path / "metadata.py"
    version_file.write_text("CHANGELOG = 'x'\n", encoding="utf-8")

    assert upload.get_local_version(version_file) == upload.application_version.__version__
