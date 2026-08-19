from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, Mock

import pytest

from src.domain_models.release import Meta
from src.integrations.media import audio
from src.integrations.media.audio import AudioManager, LossyDtsDuplicateError


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "type": "WEBDL",
        "is_disc": "",
        "dual_audio": False,
        "no_dual": False,
        "no_dub": False,
        "original_language": "English",
        "trackers": [],
        "mediainfo": {"media": {"track": []}},
        "unattended": True,
        "unattended_confirm": False,
        "manual_language": "",
        "base_dir": "",
        "uuid": "audio",
        "folder_id": "audio",
        "silent": False,
        "bloated": False,
    }
    state.update(values)
    return Meta(state)


def _mi(*tracks: dict[str, object]) -> dict[str, object]:
    return {"media": {"track": list(tracks)}}


def _audio_track(**values: object) -> dict[str, object]:
    state: dict[str, object] = {
        "@type": "Audio",
        "Format": "AAC",
        "Channels": "2",
        "Language": "English",
    }
    state.update(values)
    return state


def test_language_normalization_and_equivalence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert audio._canonical_language_code(None) == ""
    assert audio._canonical_language_code("English") == "en"
    assert audio._languages_equivalent("zh", "cmn")
    assert audio._languages_equivalent("no", "nb")
    assert audio._languages_equivalent("en", "en")
    assert not audio._languages_equivalent("", "en")
    assert not audio._languages_equivalent("fr", "de")

    monkeypatch.setattr(audio.langcodes.Language, "get", lambda _value: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(audio.langcodes, "find", lambda _value: SimpleNamespace(language="xx"))
    assert audio._canonical_language_code("Unknown") == "xx"
    monkeypatch.setattr(audio.langcodes, "find", lambda _value: (_ for _ in ()).throw(LookupError("bad")))
    assert audio._canonical_language_code(" Weird ") == "weird"


def test_channel_count_helpers_cover_all_modes() -> None:
    assert audio.determine_channel_count(None, None, "", "") == "Unknown"
    assert audio.determine_channel_count("6 channels", "L R C LFE Ls Rs", "", "AAC") == "5.1"
    assert audio.determine_channel_count("8 / 6", "L R C LFE Ls Rs Tfl Tfr", "Atmos", "E-AC-3") == "5.1.2"
    assert audio.determine_channel_count(2, None, "", "AAC") == "2.0"
    assert audio.is_atmos_or_immersive_audio("JOC", "AAC", None)
    assert audio.is_atmos_or_immersive_audio("", "DTS:X", None)
    assert audio.is_atmos_or_immersive_audio("", "AAC", "L R Tfc")
    assert not audio.is_atmos_or_immersive_audio("", "AAC", "L R")
    assert audio.handle_atmos_channel_count(6, "L R C LFE Ls Rs") == "5.1"
    assert audio.handle_atmos_channel_count(6, "L R C Ls Rs Tfc") == "5.0.1"
    assert audio.parse_atmos_layout(None) == (0, 0, 0)
    assert audio.parse_atmos_layout(" L R C LFE TFL TFR UNKNOWN ") == (3, 1, 2)
    assert audio.parse_channel_layout(8, "L R C LFE LFE Ls Rs") == "6.2"
    assert audio.parse_channel_layout(8, "object based") == "7.1"
    assert audio.parse_channel_layout(1, "MONO") == "1.0"
    assert audio.parse_channel_layout(2, "L R") == "2.0"
    assert audio.parse_channel_layout(5, "L R C Ls Rs") == "5.0"
    for channels, expected in ((1, "1.0"), (2, "2.0"), (3, "2.1"), (4, "3.1"), (5, "4.1"), (6, "5.1"), (7, "6.1"), (8, "7.1"), (9, "8.1")):
        assert audio.fallback_channel_count(channels) == expected


def test_audio_manager_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = ("AAC 2.0", "2.0", False)
    mocked = AsyncMock(return_value=expected)
    monkeypatch.setattr(audio, "_get_audio_v2", mocked)
    manager = AudioManager({"DEFAULT": {}})
    meta = _meta()
    assert asyncio.run(manager.get_audio_v2({}, meta, None)) == expected
    mocked.assert_awaited_once()


def test_audio_v2_stream_order_defaults_silent_and_codec_matrix() -> None:
    config = {"DEFAULT": {}, "TRACKERS": {}}
    tracks = [
        {"@type": "General"},
        _audio_track(StreamOrder="2", Format="AAC", Format_Commercial="AAC LC", Channels="2", Language="zxx", Default="Yes"),
        _audio_track(StreamOrder="1", Format="E-AC-3", Format_AdditionalFeatures="JOC", Channels="6", ChannelLayout="L R C LFE Ls Rs", Default="Yes"),
    ]
    meta = _meta(original_language="en")
    value, chan, commentary = asyncio.run(audio._get_audio_v2(config, _mi(*tracks), meta, None))
    assert value == "DD+ 5.1 Atmos" and chan == "5.1" and not commentary
    assert meta.has_multiple_default_audio_tracks is True

    cases = [
        (_audio_track(Format="DTS", Format_AdditionalFeatures="XLL", Channels=6), "DTS-HD MA"),
        (_audio_track(Format="DTS", Format_AdditionalFeatures="XLL X", Channels=8), "DTS:X"),
        (_audio_track(Format="MPEG Audio", Format_Profile="Layer 2", Channels=2), "MP2"),
        (_audio_track(Format="MPEG Audio", Format_Profile="Layer 3", Channels=2), "MP3"),
        (_audio_track(Format="AC-3", Channels=8), "DD+"),
        (_audio_track(Format="FLAC", Channels=2, Title="Auro3D main"), "FLAC 2.0 Auro3D"),
    ]
    for track, expected in cases:
        result, _chan, _commentary = asyncio.run(audio._get_audio_v2(config, _mi(track), _meta(), None))
        assert expected in result


def test_audio_v2_ids_invalid_order_pcm_explicit_and_unknown_codec() -> None:
    config = {"DEFAULT": {}, "TRACKERS": {}}
    tracks = [
        _audio_track(StreamOrder="bad", Format="AAC", Channels=2),
        _audio_track(StreamOrder="also-bad", Format="PCM", Channels=1),
    ]
    meta = _meta(type="ENCODE")
    result, chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(*tracks), meta, None))
    assert result.startswith("AAC") and chan == "2.0" and meta.non_disc_has_pcm_audio_tracks

    ids = [
        _audio_track(ID="128 (0x80)", Format="AAC", Channels=2),
        _audio_track(ID="2", Format="FLAC", Channels=2),
    ]
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(*ids), _meta(), None))
    assert result.startswith("FLAC")

    unknown_ids = [_audio_track(ID="bad", Format="UnknownCodec", Format_Settings="Explicit", Channels="bad")]
    result, chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(*unknown_ids), _meta(), None))
    assert result == "UnknownCodec Unknown" and chan == "Unknown"


def test_audio_v2_dual_dubbed_commentary_and_language_bloat(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {}, "TRACKERS": {}}
    meta = _meta(dual_audio=True)
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(_audio_track()), meta, None))
    assert result.startswith("Dual-Audio")

    tracks = [
        _audio_track(Language="English", Title="Main"),
        _audio_track(Language="French", Title="French"),
        _audio_track(Language="Japanese", Title="Japanese"),
        _audio_track(Language="Spanish", Title="Director Commentary"),
        _audio_track(Language="German", Title="Compatibility"),
    ]
    bloat = Mock()
    monkeypatch.setattr(audio, "bloated_check", bloat)
    meta = _meta(original_language="French")
    result, _chan, commentary = asyncio.run(audio._get_audio_v2(config, _mi(*tracks), meta, None))
    assert result.startswith("Dual-Audio") and commentary and meta.dual_audio
    bloat.assert_called_once()

    dubbed = _meta(original_language="French", no_dual=True)
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(_audio_track(Language="English")), dubbed, None))
    assert result.startswith("Dubbed")


def test_audio_v2_bdinfo_plain_and_atmos_media_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {}, "TRACKERS": {}}
    bdinfo = {"audio": [{"codec": "DTS-HD Master Audio", "channels": "5.1", "atmos_why_you_be_like_this": ""}]}
    result, chan, _ = asyncio.run(audio._get_audio_v2(config, {}, _meta(is_disc="BDMV", type="DISC"), bdinfo))
    assert result == "DTS-HD MA 5.1" and chan == "5.1"

    directory = tmp_path / "tmp" / "atmos"
    directory.mkdir(parents=True)
    payload = _mi(_audio_track(Format="MLP FBA", Format_AdditionalFeatures="16-ch", Channels=8, ChannelLayout="L R C LFE Ls Rs Tfl Tfr"))
    (directory / "MediaInfo.json").write_text(json.dumps(payload), encoding="utf-8")
    common = SimpleNamespace(get_bdmv_mediainfo=AsyncMock(return_value={"media": {}}))
    monkeypatch.setattr(audio, "Common", lambda _config: common)
    meta = _meta(base_dir=str(tmp_path), uuid="atmos", is_disc="BDMV", type="DISC")
    bdinfo = {"audio": [{"codec": "Dolby TrueHD Audio", "channels": "7.1", "atmos_why_you_be_like_this": "Atmos"}]}
    result, chan, _ = asyncio.run(audio._get_audio_v2(config, {}, meta, bdinfo))
    assert "TrueHD" in result and "Atmos" in result and chan == "5.1.2"

    (directory / "MediaInfo.json").write_text("bad-json", encoding="utf-8")
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, {}, meta, bdinfo))
    assert result == ""


def test_bloated_check_registry_rules_and_language_display(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations.trackers import registry

    class Reject:
        allows_bloated_audio = False
        allowed_bloated_audio_languages: ClassVar[tuple[str, ...]] = ()
        reject_english_original_bloat = True

    class Warn:
        allows_bloated_audio = False
        allowed_bloated_audio_languages: ClassVar[tuple[str, ...]] = ()
        reject_english_original_bloat = False

    class AllowFrench:
        allows_bloated_audio = False
        allowed_bloated_audio_languages = "fr"
        reject_english_original_bloat = False

    monkeypatch.setitem(registry.tracker_class_map, "REJECT", Reject)
    monkeypatch.setitem(registry.tracker_class_map, "WARN", Warn)
    monkeypatch.setitem(registry.tracker_class_map, "ALLOW", AllowFrench)
    meta = _meta(trackers=["REJECT", "WARN", "ALLOW", "MISSING"])
    audio.bloated_check(meta, ["fr-FR", "de"], is_eng_original_with_non_eng=True)
    assert "REJECT" not in meta.trackers and meta.bloated

    monkeypatch.setattr(audio.langcodes.Language, "get", lambda _value: (_ for _ in ()).throw(ValueError("bad")))
    meta = _meta(trackers=["WARN"])
    audio.bloated_check(meta, "xx")
    assert meta.bloated


def _dts_pair() -> list[dict[str, object]]:
    common = {"Duration": "100", "FrameRate": "24", "FrameCount": "2400", "Language": "English"}
    return [
        _audio_track(Format="DTS", Format_Commercial_IfAny="DTS-HD Master Audio", **common),
        _audio_track(Format="DTS", Format_Commercial_IfAny="DTS", **common),
    ]


def test_dts_core_duplicate_all_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(mediainfo=_mi(*_dts_pair()))
    with pytest.raises(LossyDtsDuplicateError):
        audio.dts_core_additional_check(meta)

    attended = _meta(mediainfo=_mi(*_dts_pair()), unattended=False)
    monkeypatch.setattr(audio.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    audio.dts_core_additional_check(attended)

    monkeypatch.setattr(audio.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    with pytest.raises(LossyDtsDuplicateError):
        audio.dts_core_additional_check(attended)

    monkeypatch.setattr(audio.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("prompt failed")))
    with pytest.raises(LossyDtsDuplicateError):
        audio.dts_core_additional_check(attended)

    reverse = list(reversed(_dts_pair()))
    with pytest.raises(LossyDtsDuplicateError):
        audio.dts_core_additional_check(_meta(mediainfo=_mi(*reverse)))

    empty_properties = [
        _audio_track(Format="DTS", Format_Commercial_IfAny="DTS-HD Master Audio", Duration=None, FrameRate=None, FrameCount=None, Language=None),
        _audio_track(Format="DTS", Format_Commercial_IfAny="DTS", Duration=None, FrameRate=None, FrameCount=None, Language=None),
    ]
    audio.dts_core_additional_check(_meta(mediainfo=_mi(*empty_properties)))


def test_remaining_audio_v2_and_bloat_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {}, "TRACKERS": {}}

    invalid_ids = [
        _audio_track(ID="bad", Format="AAC", Channels=2),
        _audio_track(ID="also-bad", Format="FLAC", Channels=2),
    ]
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(*invalid_ids), _meta(), None))
    assert result.startswith("AAC")

    silent = _meta()
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(_audio_track(Language="zxx")), silent, None))
    assert result and silent.silent

    class BrokenLayout(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            if key == "ChannelLayout":
                raise RuntimeError("layout failed")
            return super().get(key, default)

    broken = BrokenLayout(_audio_track(Channels=6))
    result, chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(broken), _meta(), None))
    assert result and chan == "5.1"

    duplicate = _meta(mediainfo=_mi(*_dts_pair()))
    with pytest.raises(LossyDtsDuplicateError):
        asyncio.run(audio._get_audio_v2(config, duplicate.mediainfo, duplicate, None))

    original_bloated_check = audio.bloated_check
    monkeypatch.setattr(audio, "bloated_check", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bloat failed")))
    multilingual = _mi(
        _audio_track(Language="English"),
        _audio_track(Language="French"),
        _audio_track(Language="Japanese"),
    )
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, multilingual, _meta(original_language="French"), None))
    assert result
    monkeypatch.setattr(audio, "bloated_check", original_bloated_check)

    additional_dict = _audio_track(Format="AAC", Format_AdditionalFeatures={"bad": True})
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(additional_dict), _meta(), None))
    assert result.startswith("AAC")

    commercial_atmos = _audio_track(Format="MLP FBA", Format_Commercial="Dolby TrueHD Atmos", Channels=8)
    result, _chan, _ = asyncio.run(audio._get_audio_v2(config, _mi(commercial_atmos), _meta(), None))
    assert "TrueHD" in result and "Atmos" in result

    from src.integrations.trackers import registry

    class Allow:
        allows_bloated_audio = True
        allowed_bloated_audio_languages: ClassVar[tuple[str, ...]] = ()
        reject_english_original_bloat = False

    class Warn:
        allows_bloated_audio = False
        allowed_bloated_audio_languages: ClassVar[tuple[str, ...]] = ()
        reject_english_original_bloat = False

    monkeypatch.setitem(registry.tracker_class_map, "ALLOWONLY", Allow)
    allowed = _meta(trackers=["ALLOWONLY"])
    audio.bloated_check(allowed, "fr")
    assert not allowed.bloated

    monkeypatch.setitem(registry.tracker_class_map, "WARNONLY", Warn)
    warning = _meta(trackers=["WARNONLY"])
    audio.bloated_check(warning, "de", is_eng_original_with_non_eng=True)
    assert warning.bloated


def test_bloat_registry_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object):
        if name == "src.integrations.trackers.registry":
            raise RuntimeError("registry unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    meta = _meta(trackers=["MISSING"])
    audio.bloated_check(meta, "fr")
    assert meta.bloated
