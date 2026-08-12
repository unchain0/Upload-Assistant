from matplotlib import font_manager

from src.audio_spectrogram import (
    MAX_TIME_BINS,
    _resolve_plot_font,
    _sanitize_plot_text,
    get_spectrogram_sources,
    get_stft_parameters,
    prompt_audio_stream_positions,
    select_audio_streams,
)


def test_prompt_audio_stream_positions_uses_cli_ui_and_defaults_to_all(monkeypatch):
    recorded = {}

    def ask_string(*question, default=None):
        recorded["question"] = question
        recorded["default"] = default
        return

    monkeypatch.setattr("src.audio_spectrogram.cli_ui.ask_string", ask_string)

    assert prompt_audio_stream_positions() == "all"  # noqa: S101
    assert recorded == {"question": ("Select audio stream positions (e.g. 0,1 or all)",), "default": "all"}  # noqa: S101


def test_select_audio_streams_accepts_positions_and_removes_duplicates():
    streams = [{"index": 2}, {"index": 5}, {"index": 9}]

    assert select_audio_streams(streams, "2,0,2") == [streams[2], streams[0]]  # noqa: S101


def test_select_audio_streams_only_accepts_all_for_every_stream():
    streams = [{"index": 2}, {"index": 5}, {"index": 9}]

    assert select_audio_streams(streams, "3") == []  # noqa: S101
    assert select_audio_streams(streams, "all") == streams  # noqa: S101


def test_get_spectrogram_sources_uses_every_music_track_and_applies_limit(tmp_path):
    tracks = [tmp_path / f"track-{number}.flac" for number in range(3)]
    for track in tracks:
        track.touch()

    assert get_spectrogram_sources("MUSIC", [str(track) for track in tracks], None, 2) == tracks[:2]  # noqa: S101


def test_get_spectrogram_sources_only_uses_audio_files_for_audiobooks(tmp_path):
    chapter = tmp_path / "chapter-01.m4b"
    cover = tmp_path / "cover.jpg"
    chapter.touch()
    cover.touch()

    assert get_spectrogram_sources("BOOK", [str(cover), str(chapter)], None, 12) == [chapter]  # noqa: S101


def test_stft_parameters_bound_the_number_of_time_bins_for_long_audio():
    sample_count = 600 * 48000

    n_fft, hop_length = get_stft_parameters(sample_count)

    assert n_fft == 2048  # noqa: S101
    assert sample_count / hop_length <= MAX_TIME_BINS  # noqa: S101


def test_sanitize_plot_text_keeps_ascii_when_unicode_supported():
    assert _sanitize_plot_text("Shrouding the Heavens", True) == "Shrouding the Heavens"  # noqa: S101


def test_sanitize_plot_text_replaces_non_ascii_when_unicode_not_supported():
    assert _sanitize_plot_text("Shrouding the Heavens 遮天", False) == "Shrouding the Heavens ??"  # noqa: S101


def test_resolve_plot_font_prefers_matplotlib_name_match(monkeypatch):
    fallback_font = "noto-cjk-fallback.otf"

    def fake_findfont(*_args, **_kwargs):
        if _args and _args[0] == "Noto Sans CJK SC":
            return fallback_font
        raise ValueError("not found")

    class FakeFontProperties:
        def __init__(self, fname=None):
            self._fname = fname

        def get_name(self) -> str:
            if self._fname == fallback_font:
                return "Noto Sans CJK SC"
            return "Unknown"

    monkeypatch.setattr(font_manager, "findfont", fake_findfont)
    monkeypatch.setattr(font_manager, "FontProperties", FakeFontProperties)
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda _path: None)
    monkeypatch.setattr("src.audio_spectrogram.ft2font.FT2Font", lambda _font_path: object())

    assert _resolve_plot_font() == ("Noto Sans CJK SC", True, fallback_font)  # noqa: S101


def test_resolve_plot_font_finds_cjk_font_from_system_font_files(monkeypatch):
    def fake_findfont(*_args, **_kwargs):
        raise ValueError("not found")

    monkeypatch.setattr(font_manager, "findfont", fake_findfont)
    monkeypatch.setattr(
        font_manager,
        "findSystemFonts",
        lambda *_args, **_kwargs: [
            "/usr/share/fonts/example/regular.ttf",
            "/usr/share/fonts/google-noto-sans-mono-cjk-vf-fonts/NotoSansMonoCJK-VF.ttc",
        ],
    )

    class FakeFontProperties:
        def __init__(self, fname=None, family=None):  # noqa: ARG002
            self._fname = fname

        def get_name(self) -> str:
            if self._fname and "NotoSansMonoCJK-VF" in self._fname:
                return "Noto Sans Mono CJK SC"
            return "DejaVu Sans"

    monkeypatch.setattr(font_manager, "FontProperties", FakeFontProperties)
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda _path: None)
    monkeypatch.setattr("src.audio_spectrogram.ft2font.FT2Font", lambda _font_path: object())

    assert _resolve_plot_font() == ("Noto Sans Mono CJK SC", True, "/usr/share/fonts/google-noto-sans-mono-cjk-vf-fonts/NotoSansMonoCJK-VF.ttc")  # noqa: S101


def test_resolve_plot_font_marks_wenquanyi_font_as_unicode_supported(monkeypatch):
    def fake_findfont(*_args, **_kwargs):
        _name = _args[0] if _args else _kwargs.get("_name", "")
        if _name == "WenQuanYi Zen Hei":
            return "wenquanyi-zen-hei.otf"
        raise ValueError("not found")
    expected_font_path = "wenquanyi-zen-hei.otf"

    class FakeFontProperties:
        def __init__(self, fname=None):
            self._fname = fname

        def get_name(self) -> str:
            return "WenQuanYi Zen Hei"

    monkeypatch.setattr(font_manager, "findfont", fake_findfont)
    monkeypatch.setattr(font_manager, "FontProperties", FakeFontProperties)
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda _path: None)
    monkeypatch.setattr("src.audio_spectrogram.ft2font.FT2Font", lambda _font_path: object())

    assert _resolve_plot_font() == ("WenQuanYi Zen Hei", True, expected_font_path)  # noqa: S101


def test_resolve_plot_font_prefers_cjk_candidate_when_default_font_finds_first(monkeypatch):
    def fake_findfont(*args, **_kwargs):
        requested = args[0]
        if requested == "Noto Sans":
            return "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
        if requested == "WenQuanYi Zen Hei":
            return "/usr/share/fonts/wqy/wqy-zenhei.ttc"
        raise ValueError("not found")

    monkeypatch.setattr(font_manager, "findfont", fake_findfont)

    class FakeFontProperties:
        def __init__(self, fname=None):
            self._fname = fname

        def get_name(self) -> str:
            if "NotoSans-Regular" in str(self._fname):
                return "Noto Sans"
            return "WenQuanYi Zen Hei"

    monkeypatch.setattr(font_manager, "FontProperties", FakeFontProperties)
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda _path: None)
    monkeypatch.setattr("src.audio_spectrogram.ft2font.FT2Font", lambda _font_path: object())

    assert _resolve_plot_font() == ("WenQuanYi Zen Hei", True, "/usr/share/fonts/wqy/wqy-zenhei.ttc")  # noqa: S101


def test_resolve_plot_font_skips_unloadable_cjk_fonts(monkeypatch):
    bad_font = "/usr/share/fonts/custom/BadSansCJK-VF.ttc"
    good_font = "/usr/share/fonts/custom/WenQuanYiZenHei.otf"

    def fake_findfont(*args, **_kwargs):
        requested = args[0]
        if requested == "WenQuanYi Zen Hei":
            return bad_font
        if requested == "Noto Sans CJK SC":
            return bad_font
        raise ValueError("not found")

    def fake_find_system_fonts(*_args, **_kwargs):
        return [
            bad_font,
            good_font,
        ]

    class FakeFontProperties:
        def __init__(self, fname=None):
            self._fname = fname

        def get_name(self) -> str:
            if self._fname == good_font:
                return "WenQuanYi Zen Hei"
            return "Noto Sans CJK SC"

    class FakeFT2Font:
        def __init__(self, font_path):
            if font_path == bad_font:
                raise RuntimeError("Can not load face")

    monkeypatch.setattr(font_manager, "findfont", fake_findfont)
    monkeypatch.setattr(font_manager, "findSystemFonts", fake_find_system_fonts)
    monkeypatch.setattr(font_manager, "FontProperties", FakeFontProperties)
    monkeypatch.setattr(font_manager.fontManager, "addfont", lambda _path: None)
    monkeypatch.setattr("src.audio_spectrogram.ft2font.FT2Font", FakeFT2Font)

    assert _resolve_plot_font() == ("WenQuanYi Zen Hei", True, good_font)  # noqa: S101
