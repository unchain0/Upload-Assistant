from __future__ import annotations

from pathlib import Path

import pytest

from src.domain_models.configuration import ConfigurationSourceKind
from src.domain_models.errors import ConfigurationNotFoundError, ConfigurationSyntaxError
from src.integrations.configuration import PythonConfigurationRepository
from src.services.configuration_service import ConfigurationService


def _write_config(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"config = {value!r}\n", encoding="utf-8")


def _service(tmp_path: Path, *, explicit_path: Path | None = None) -> ConfigurationService:
    return ConfigurationService(
        PythonConfigurationRepository(),
        runtime_path=tmp_path / "state" / "data" / "config.py",
        legacy_path=tmp_path / "checkout" / "data" / "config.py",
        defaults_path=tmp_path / "checkout" / "data" / "example_config.py",
        explicit_path=explicit_path,
    )


def test_reconciles_empty_runtime_from_legacy_without_overwriting_runtime_values(tmp_path: Path) -> None:
    runtime_path = tmp_path / "state" / "data" / "config.py"
    _write_config(
        runtime_path,
        {
            "DEFAULT": {
                "tmdb_api": "",
                "img_host_1": "",
                "onlyimage_api": "",
                "runtime_preference": "keep-me",
            },
            "TRACKERS": {"TORRENTHR": {"img_api": "get this from the forum post"}},
        },
    )
    _write_config(
        tmp_path / "checkout" / "data" / "config.py",
        {
            "DEFAULT": {
                "tmdb_api": " configured-v3-key ",
                "img_host_1": "onlyimage",
                "onlyimage_api": "configured-image-token",
                "runtime_preference": "legacy-value",
            },
            "TRACKERS": {"TORRENTHR": {"img_api": "configured-tracker-image-token"}},
        },
    )
    _write_config(
        tmp_path / "checkout" / "data" / "example_config.py",
        {
            "DEFAULT": {
                "tmdb_api": "",
                "img_host_1": "",
                "onlyimage_api": "",
                "runtime_preference": "default-value",
            },
            "TRACKERS": {"TORRENTHR": {"img_api": "get this from the forum post"}},
        },
    )

    loaded = _service(tmp_path).load()

    defaults = loaded.section("DEFAULT")
    tracker = loaded.section("TRACKERS")["TORRENTHR"]
    assert loaded.source.kind is ConfigurationSourceKind.RUNTIME
    assert defaults["tmdb_api"] == " configured-v3-key "
    assert defaults["img_host_1"] == "onlyimage"
    assert defaults["onlyimage_api"] == "configured-image-token"
    assert defaults["runtime_preference"] == "keep-me"
    assert tracker["img_api"] == "configured-tracker-image-token"
    assert runtime_path.with_name("config.py.pre-masa.bak").is_file()


def test_configured_runtime_is_authoritative(tmp_path: Path) -> None:
    runtime_path = tmp_path / "state" / "data" / "config.py"
    _write_config(runtime_path, {"DEFAULT": {"tmdb_api": "runtime-key", "img_host_1": "imgbox"}})
    _write_config(
        tmp_path / "checkout" / "data" / "config.py",
        {"DEFAULT": {"tmdb_api": "legacy-key", "img_host_1": "onlyimage"}},
    )
    _write_config(
        tmp_path / "checkout" / "data" / "example_config.py",
        {"DEFAULT": {"tmdb_api": "", "img_host_1": ""}},
    )

    loaded = _service(tmp_path).load()

    assert loaded.section("DEFAULT")["tmdb_api"] == "runtime-key"
    assert loaded.section("DEFAULT")["img_host_1"] == "imgbox"
    assert not runtime_path.with_name("config.py.pre-masa.bak").exists()


def test_explicit_configuration_is_materialized_for_all_legacy_consumers(tmp_path: Path) -> None:
    explicit = tmp_path / "selected.py"
    _write_config(explicit, {"DEFAULT": {"tmdb_api": "explicit-key", "img_host_1": "pixhost"}})
    _write_config(tmp_path / "checkout" / "data" / "example_config.py", {"DEFAULT": {"tmdb_api": "", "img_host_1": ""}})

    loaded = _service(tmp_path, explicit_path=explicit).load()

    runtime_path = tmp_path / "state" / "data" / "config.py"
    assert loaded.source.kind is ConfigurationSourceKind.RUNTIME
    assert loaded.section("DEFAULT")["tmdb_api"] == "explicit-key"
    assert runtime_path.read_text(encoding="utf-8") == explicit.read_text(encoding="utf-8")


def test_defaults_are_materialized_when_no_user_configuration_exists(tmp_path: Path) -> None:
    defaults_path = tmp_path / "checkout" / "data" / "example_config.py"
    _write_config(defaults_path, {"DEFAULT": {"tmdb_api": "", "img_host_1": ""}})

    loaded = _service(tmp_path).load()

    assert loaded.source.kind is ConfigurationSourceKind.RUNTIME
    assert (tmp_path / "state" / "data" / "config.py").is_file()


def test_missing_explicit_configuration_is_semantic_error(tmp_path: Path) -> None:
    _write_config(tmp_path / "checkout" / "data" / "example_config.py", {"DEFAULT": {}})

    with pytest.raises(ConfigurationNotFoundError, match="Explicit configuration file not found"):
        _service(tmp_path, explicit_path=tmp_path / "missing.py").load()


def test_repository_rejects_executable_configuration(tmp_path: Path) -> None:
    path = tmp_path / "config.py"
    path.write_text("config = dict(DEFAULT={})\n", encoding="utf-8")

    with pytest.raises(ConfigurationSyntaxError, match="literal mapping"):
        PythonConfigurationRepository().load(path, ConfigurationSourceKind.RUNTIME)


def test_repository_reloads_exact_file_without_module_cache(tmp_path: Path) -> None:
    path = tmp_path / "config.py"
    repository = PythonConfigurationRepository()
    _write_config(path, {"DEFAULT": {"tmdb_api": "first"}})
    first = repository.load(path, ConfigurationSourceKind.RUNTIME)
    _write_config(path, {"DEFAULT": {"tmdb_api": "second"}})
    second = repository.load(path, ConfigurationSourceKind.RUNTIME)

    assert first.section("DEFAULT")["tmdb_api"] == "first"
    assert second.section("DEFAULT")["tmdb_api"] == "second"
