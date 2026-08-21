from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.domain_models.release_group import is_valid_prefixed_release_group, is_valid_release_group, release_group_has_episode_syntax, release_group_name
from src.integrations.filesystem import tags


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "anime": False,
        "is_disc": "",
        "tv_pack": False,
        "keep_folder": False,
        "category": "MOVIE",
        "uuid": "Release.1080p-GROUP",
        "title": "Release",
        "author": "",
        "scene": False,
        "path": "Release.1080p-GROUP.mkv",
        "tag": "",
        "type": "ENCODE",
        "personalrelease": False,
        "description_template": None,
        "base_dir": ".",
    }
    state.update(values)
    return Meta(state)


def test_guessit_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tags.guessit_module, "guessit", lambda value, options=None: {"title": value, "options": options})
    assert tags.guessit_fn("Release", {"type": "movie"}) == {"title": "Release", "options": {"type": "movie"}}


def test_get_tag_anime_and_non_anime_paths(tmp_path: Path) -> None:
    assert asyncio.run(tags.get_tag("[SubsPlease] Show - 01.mkv", _meta(anime=True))) == "-SubsPlease"
    assert asyncio.run(tags.get_tag("Show.S01E01.1080p-GROUP.mkv", _meta(anime=True))) == "-GROUP"

    directory = tmp_path / "Movie.2026.1080p-GROUP"
    directory.mkdir()
    assert asyncio.run(tags.get_tag(str(directory), _meta())) == "-GROUP"

    pack = _meta(category="TV", tv_pack=True, uuid="Show.S01.1080p-PACKGROUP")
    assert asyncio.run(tags.get_tag("ignored.mkv", pack)) == "-PACKGROUP"

    folder = _meta(category="MOVIE", keep_folder=True, uuid="Movie.2026.1080p-FOLDER")
    assert asyncio.run(tags.get_tag("ignored.mkv", folder)) == "-FOLDER"

    assert asyncio.run(tags.get_tag("Movie.2026.1080p.x265-GROUP", _meta())) == "-GROUP"
    assert asyncio.run(tags.get_tag("Movie.2026.1080p-GROUP.mkv", _meta())) == "-GROUP"


@pytest.mark.parametrize(
    "filename",
    [
        "[Gecko]_False_Memory_-_S01E05_[BILI.WEB-DL_1080P_HEVC_AAC_D-SUB][5A86C56D].mkv",
        "[SubsPlease] Title - 05 (1080p).mkv",
        "[Erai-raws] Title - 05 [1080p].mkv",
        "[Group] Title S01E05 [1080p].mkv",
    ],
)
def test_get_tag_preserves_valid_prefix_group_for_tv_without_anime_flag(filename: str) -> None:
    expected = filename.split("]", 1)[0].lstrip("[")
    assert asyncio.run(tags.get_tag(filename, _meta(category="TV", anime=False))) == f"-{expected}"


@pytest.mark.parametrize("candidate", ["S01", "S01E05", "E05", "01x05", "EP05", "Episode 05", "-_S01E05_"])
def test_release_group_semantics_reject_episode_tokens(candidate: str) -> None:
    assert release_group_has_episode_syntax(candidate)
    assert not is_valid_release_group(candidate)


def test_release_group_semantics_validate_prefix_candidates() -> None:
    assert release_group_name("-Gecko") == "Gecko"
    assert release_group_name("Gecko") == "Gecko"
    assert is_valid_release_group("Gecko")
    assert is_valid_prefixed_release_group("Gecko")
    assert not is_valid_prefixed_release_group("5A86C56D")
    assert not is_valid_prefixed_release_group("1080p")
    assert not is_valid_prefixed_release_group("BILI.WEB-DL_1080P_HEVC_AAC_D-SUB")


@pytest.mark.parametrize("filename", ["Show-S01.mkv", "Show-S01E05.mkv", "Show-E05.mkv", "Show-01x05.mkv", "Show-EP05.mkv", "Show-Episode 05.mkv", "Show-_S01E05_.mkv"])
def test_get_tag_never_returns_episode_syntax_as_release_group(filename: str) -> None:
    assert asyncio.run(tags.get_tag(filename, _meta(category="TV", anime=False))) == ""


def test_get_tag_book_game_title_author_and_hyphen_guards() -> None:
    book = _meta(category="BOOK", title="Title", author="Author", uuid="Author - Title.epub")
    assert asyncio.run(tags.get_tag("Author - Title.epub", book)) == ""

    title_match = _meta(category="BOOK", title="Example Group", author="Author", uuid="Book-Example.Group.epub")
    assert asyncio.run(tags.get_tag("Book-Example.Group.epub", title_match)) == ""

    author_match = _meta(category="BOOK", title="Book", author="Alice Writer", uuid="Book-Alice.Writer.epub")
    assert asyncio.run(tags.get_tag("Book-Alice.Writer.epub", author_match)) == ""

    spider = _meta(category="BOOK", title="Spider-Man", author="Author", uuid="Spider-Man.epub")
    assert asyncio.run(tags.get_tag("Spider-Man.epub", spider)) == ""

    game = _meta(category="GAME", title="Game", author="", uuid="Game - Deluxe Edition.iso")
    assert asyncio.run(tags.get_tag("Game - Deluxe Edition.iso", game)) == ""


def test_get_tag_rewrites_z0n3_long_scene_and_space_group() -> None:
    assert asyncio.run(tags.get_tag("Movie.1080p-Z0N3.mkv", _meta())) == "-D-Z0N3"
    assert asyncio.run(tags.get_tag("Movie.1080p-VERYLONGGROUPNAME.mkv", _meta(scene=False))) == ""
    assert asyncio.run(tags.get_tag("Movie.1080p-VERYLONGGROUPNAME.mkv", _meta(scene=True))) == "-VERYLONGGROUPNAME"
    assert asyncio.run(tags.get_tag("Movie 1080p BONE.mkv", _meta())) == "-BONE"


def test_get_tag_disc_guessit_success_error_bdmv_validation_and_generic_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tags, "guessit_fn", lambda _value, _options=None: {"release_group": "DISCGRP"})
    assert asyncio.run(tags.get_tag("DVD Folder", _meta(is_disc="DVD"))) == "-DISCGRP"
    assert asyncio.run(tags.get_tag("BDMV DISCGRP", _meta(is_disc="BDMV"))) == "-DISCGRP"
    assert asyncio.run(tags.get_tag("BDMV OTHER", _meta(is_disc="BDMV"))) == ""

    monkeypatch.setattr(tags, "guessit_fn", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad guess")))
    assert asyncio.run(tags.get_tag("DVD Folder", _meta(is_disc="DVD"))) == ""

    monkeypatch.setattr(tags, "guessit_fn", lambda *_args, **_kwargs: {"release_group": "HD.MA.5.1"})
    assert asyncio.run(tags.get_tag("DVD Folder", _meta(is_disc="DVD"))) == ""
    monkeypatch.setattr(tags, "guessit_fn", lambda *_args, **_kwargs: {"release_group": "UNTOUCHED"})
    assert asyncio.run(tags.get_tag("DVD Folder", _meta(is_disc="DVD"))) == ""


def test_tag_override_in_name_type_personal_template_and_arbitrary_values(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "tags.json").write_text(
        json.dumps(
            {
                "GROUP": {
                    "in_name": "GROUP",
                    "type": "REMUX",
                    "personalrelease": "TRUE",
                    "template": "template.txt",
                    "source": "BluRay",
                }
            }
        ),
        encoding="utf-8",
    )
    meta = _meta(base_dir=str(tmp_path), path="Movie-GROUP.mkv", type="ENCODE")
    result = asyncio.run(tags.tag_override(meta))
    assert result.tag == "-GROUP"
    assert result.type == "REMUX"
    assert result.personalrelease is True
    assert result.description_template == "template.txt"
    assert result.source == "BluRay"

    meta = _meta(base_dir=str(tmp_path), path="Movie.mkv", tag="-GROUP", type="WEBDL")
    result = asyncio.run(tags.tag_override(meta))
    assert result.type == "WEBDL"


def test_tag_override_missing_malformed_and_boolean_values(tmp_path: Path) -> None:
    meta = _meta(base_dir=str(tmp_path))
    assert asyncio.run(tags.tag_override(meta)) is meta

    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "tags.json").write_text(json.dumps({"BAD": None}), encoding="utf-8")
    assert asyncio.run(tags.tag_override(meta)) is meta

    assert tags._is_true(True)
    assert tags._is_true(" true ")
    assert not tags._is_true("yes")
    assert not tags._is_true(None)
