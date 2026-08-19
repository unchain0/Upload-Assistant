from __future__ import annotations

from typing import Any

from src.services.configuration_validation_service import (
    ConfigValidationWarning,
    _as_dict,
    _validate_default_section,
    _validate_torrent_clients_section,
    _validate_trackers_section,
    _validate_usenet_section,
    format_validation_results,
    group_warnings,
    validate_config,
)


def _base_config() -> dict[str, Any]:
    return {
        "DEFAULT": {"tmdb_api": "key", "img_host_1": "imgbox"},
        "TRACKERS": {"default_trackers": ""},
    }


def test_warning_rendering_and_dict_dressing() -> None:
    assert str(ConfigValidationWarning("message")) == "message"
    assert str(ConfigValidationWarning("message", key="key")) == "[key] message"
    assert str(ConfigValidationWarning("message", section="SECTION")) == "[SECTION] message"
    assert str(ConfigValidationWarning("message", key="key", section="SECTION")) == "[SECTION][key] message"
    assert _as_dict({"key": "value"}) == {"key": "value"}
    assert _as_dict("not a dict") == {}


def test_validate_config_rejects_invalid_top_level_shapes() -> None:
    valid, errors, warnings = validate_config([])
    assert not valid and "list" in errors[0] and warnings == []

    valid, errors, _ = validate_config({})
    assert not valid
    assert "DEFAULT" in " ".join(errors) and "TRACKERS" in " ".join(errors)

    valid, errors, _ = validate_config({"DEFAULT": [], "TRACKERS": "bad"})
    assert not valid
    assert len(errors) == 2


def test_tracker_sources_client_cross_references_and_unknown_sections() -> None:
    config = _base_config()
    config["TRACKERS"] = {
        "default_trackers": [" tracker ", 1, ""],
        "TRACKER": {"api_key": "   ", "announce_url": "https://tracker/<PASSKEY>", "anon": "true"},
    }
    config["DEFAULT"].update(
        {
            "injecting_client_list": ["missing", 7, "none"],
            "searching_client_list": ["missing-search", 8],
            "default_torrent_client": "missing-default",
        }
    )
    config["TORRENT_CLIENTS"] = {
        "defined": {
            "torrent_client": "unknown",
            "linking": "copy",
            "local_path": ["/a", "/b"],
            "remote_path": ["/c"],
        },
        "bad": "not a mapping",
    }
    config["CUSTOM"] = {}

    valid, errors, warnings = validate_config(config, active_trackers=["TRACKER"])

    assert not valid
    assert any("announce_url" in error for error in errors)
    rendered = "\n".join(str(warning) for warning in warnings)
    for expected in (
        "api_key is whitespace-only",
        "anon",
        "undefined client 'missing'",
        "undefined client 'missing-search'",
        "missing-default",
        "Unknown torrent_client",
        "Invalid linking",
        "matching lengths",
        "Client config must be",
        "Unknown config section",
    ):
        assert expected in rendered


def test_client_list_shape_and_default_client_variants() -> None:
    config = _base_config()
    config["DEFAULT"].update({"injecting_client_list": 7, "searching_client_list": "bad", "default_torrent_client": "client"})
    valid, _, warnings = validate_config(config)
    assert valid
    rendered = "\n".join(str(warning) for warning in warnings)
    assert "list or string" in rendered
    assert "Should be a list" in rendered
    assert "no clients defined" in rendered

    config = _base_config()
    config["TORRENT_CLIENTS"] = {"client": {"torrent_client": "qbit"}}
    config["DEFAULT"]["default_torrent_client"] = ""
    _, _, warnings = validate_config(config)
    assert any("No default_torrent_client" in warning.message for warning in warnings)

    config["DEFAULT"]["injecting_client_list"] = "client"
    _, _, warnings = validate_config(config)
    assert not any("No default_torrent_client" in warning.message for warning in warnings)


def test_usenet_required_types_numeric_and_missing_section_paths() -> None:
    config = _base_config()
    valid, errors, _ = validate_config(config, active_trackers=["USENET"])
    assert not valid and any("Missing required config section: 'USENET'" in error for error in errors)

    config["USENET"] = {"enabled": True, "host": "", "port": "bad", "username": "", "password": "", "newsgroups": "", "ssl": "yes"}
    valid, errors, warnings = validate_config(config)
    assert not valid
    assert len([error for error in errors if "[USENET]" in error]) == 4
    rendered = "\n".join(str(warning) for warning in warnings)
    assert "Cannot parse 'bad' as integer" in rendered
    assert "Expected type bool" in rendered

    errors, warnings = _validate_usenet_section({"port": "119", "connections": 4}, False)
    assert errors == [] and warnings == []


def test_default_section_warns_for_types_values_hosts_and_numeric_limits() -> None:
    errors, warnings = _validate_default_section(
        {
            "tmdb_access_token": "token",
            "debug": "yes",
            "upload_order": "later",
            "embed_dupe_links": True,
            "img_host_1": "not-a-host",
            "screens": "bad",
            "xxx_contact_sheet_animation_seconds": "bad",
            "image_upload_concurrency": -1,
            "image_upload_delay": float("inf"),
        }
    )
    assert errors == []
    rendered = "\n".join(str(warning) for warning in warnings)
    for expected in (
        "Expected type bool",
        "Invalid value",
        "Deprecated",
        "Unknown image host",
        "as integer",
        "as number",
        "Value must be >= 0",
        "finite and >= 0",
    ):
        assert expected in rendered

    errors, warnings = _validate_default_section({"tmdb_api": 7, "image_upload_concurrency": object(), "image_upload_delay": object()})
    assert errors
    rendered = "\n".join(str(warning) for warning in warnings)
    assert "Cannot parse" in rendered


def test_tracker_and_client_section_helpers_cover_missing_and_invalid_configs() -> None:
    errors, warnings = _validate_trackers_section(
        {
            "TRACKER": "bad",
            "ACTIVE": {
                "api_key": "   ",
                "announce_url": "https://tracker/<PASSKEY>",
                "useAPI": "yes",
            },
        },
        ["ACTIVE", "DRUNKENSLUG"],
    )
    assert any("DRUNKENSLUG" in error for error in errors)
    assert any("announce_url" in error for error in errors)
    rendered = "\n".join(str(warning) for warning in warnings)
    assert "default_trackers" in rendered
    assert "must be a dictionary" in rendered
    assert "api_key is whitespace-only" in rendered
    assert "useAPI" in rendered

    errors, warnings = _validate_torrent_clients_section(
        {
            "bad": [],
            "client": {
                "torrent_client": "invalid",
                "linking": "copy",
                "local_path": ["one", "two"],
                "remote_path": ["one"],
            },
        }
    )
    assert errors == []
    assert len(warnings) == 4


def test_active_image_host_requirement_and_valid_api_key() -> None:
    config = _base_config()
    config["DEFAULT"].update({"img_host_1": "imgbb", "imgbb_api": ""})
    valid, errors, _ = validate_config(config)
    assert not valid and any("imgbb_api" in error for error in errors)

    config["DEFAULT"]["imgbb_api"] = "key"
    valid, errors, _ = validate_config(config, active_imghost=" imgbb ")
    assert valid and errors == []


def test_group_and_format_validation_results() -> None:
    warnings = [
        ConfigValidationWarning("same", key="B", section="TRACKERS"),
        ConfigValidationWarning("same", key="A", section="TRACKERS"),
        ConfigValidationWarning("section only", section="DEFAULT"),
        ConfigValidationWarning("key only", key="item"),
        ConfigValidationWarning("plain"),
    ]
    grouped = group_warnings(warnings)
    assert "[TRACKERS][B, A] same" in grouped
    assert "[DEFAULT] section only" in grouped
    assert "[item] key only" in grouped
    assert "plain" in grouped

    output = format_validation_results(False, ["broken"], warnings)
    assert "Config Validation Errors" in output
    assert "Config Validation Warnings" in output
    assert "broken" in output
    assert "Warnings" not in format_validation_results(False, [], warnings, show_warnings=False)
    assert format_validation_results(True, [], []) == "Config validation passed."
    assert "passed with 1 warning" in format_validation_results(True, [], [ConfigValidationWarning("warning")])


def test_default_tracker_string_and_list_sources() -> None:
    string_config = _base_config()
    string_config["TRACKERS"] = {"default_trackers": " tracker , ", "TRACKER": {"announce_url": "https://tracker/<PASSKEY>"}}
    valid, errors, _ = validate_config(string_config)
    assert not valid and any("announce_url" in error for error in errors)

    list_config = _base_config()
    list_config["TRACKERS"] = {"default_trackers": [" tracker ", 7, ""], "TRACKER": {"announce_url": "https://tracker/<PASSKEY>"}}
    valid, errors, _ = validate_config(list_config)
    assert not valid and any("announce_url" in error for error in errors)


def test_required_default_key_contract(monkeypatch) -> None:
    from src.services import configuration_validation_service as validation

    monkeypatch.setattr(validation, "REQUIRED_DEFAULT_KEYS", {"required": str})
    errors, _ = validation._validate_default_section({"tmdb_api": "key"})
    assert errors == ["Missing required key in DEFAULT section: 'required'"]
    errors, _ = validation._validate_default_section({"tmdb_api": "key", "required": 1})
    assert errors == ["DEFAULT['required'] must be str, got int"]
    errors, _ = validation._validate_default_section({"tmdb_api": "key", "required": "value"})
    assert errors == []
