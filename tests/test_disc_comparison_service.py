from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.services import disc_comparison_service as comparison


def _meta(tmp_path: Path, summary: str = "", extended: str = "") -> Meta:
    state = tmp_path / "tmp" / "disc"
    state.mkdir(parents=True, exist_ok=True)
    if summary:
        (state / "BD_SUMMARY_00.txt").write_text(summary, encoding="utf-8")
    if extended:
        (state / "BD_SUMMARY_EXT_00.txt").write_text(extended, encoding="utf-8")
    return Meta(base_dir=str(tmp_path), uuid="disc")


def test_normalize_filter_strict_and_playlist_variations() -> None:
    content = """
      Video:  AVC / 25000 kbps
      AVC / 25000 kbps
      Audio: DTS / 1500 kbps
      Subtitle: English / 25 kbps
      Presentation Graphics English / 15.5 kbps /
      ignored line
    """
    loose = comparison.normalize_and_filter(content)
    strict = comparison.normalize_and_filter(content, strict_mode=True)
    assert len(loose) == 5
    assert strict == ["Video: AVC / 25000 kbps", "Audio: DTS / 1500 kbps", "Subtitle: English / 25 kbps"]

    summary, extended, duplicate = comparison.remove_playlist_variations(
        "Audio: DTS / DN -4dB\nSubtitle: English / 12 kbps /\n*comment",
        "",
        "Presentation Graphics English / 15,5 kbps /\nSubtitle: French / kbps",
    )
    assert "DN -4dB" not in summary
    assert "12" not in summary
    assert summary.splitlines()[-1] == "*"
    assert extended == ""
    assert "15,5" not in duplicate
    assert not duplicate.splitlines()[0].endswith("/")
    assert not duplicate.splitlines()[1].endswith("kbps")


def test_load_format_and_content_helpers(tmp_path: Path) -> None:
    meta = _meta(tmp_path, "summary", "extended")
    assert comparison.load_bdinfo_file(meta) == ("summary", "extended")
    assert comparison.load_bdinfo_file(Meta(base_dir=str(tmp_path), uuid="missing")) == ("", "")

    formatted = "[quote]<p>Disc Title: Movie<br>Video: AVC</p>[/quote]"
    cleaned = comparison.remove_formatting(formatted)
    assert "[quote]" not in cleaned and "<p>" not in cleaned
    assert "Disc Title: Movie\nVideo: AVC" in cleaned

    assert comparison.has_bdinfo_content({"bd_info": "BDINFO"}) == "BDINFO"
    assert comparison.has_bdinfo_content({"description": "Disc Label: Movie"}) == "Disc Label: Movie"
    assert comparison.has_bdinfo_content({"description": "plain"}) == ""


def test_get_relevant_lines_summary_extended_and_full_modes(tmp_path: Path) -> None:
    summary = "Video: AVC / 25000 kbps\nAudio: DTS / 1500 kbps\nSubtitle: English / 25 kbps"
    extended = "PLAYLIST REPORT:\nAudio: DTS / 1500 kbps\nPresentation Graphics English / 25 kbps"
    meta = _meta(tmp_path, summary, extended)

    source, target = comparison.get_relevant_lines(meta, "Disc Title: Movie\nAudio: DTS / 1500 kbps")
    normalized_summary, normalized_extended, _ = comparison.remove_playlist_variations(summary, extended, "")
    assert source == comparison.normalize_and_filter(normalized_summary)
    assert target == ["Audio: DTS / 1500 kbps"]

    source, target = comparison.get_relevant_lines(meta, "PLAYLIST REPORT:\nAudio: DTS / 1500 kbps")
    assert source == comparison.normalize_and_filter(normalized_extended)
    assert target == ["Audio: DTS / 1500 kbps"]

    source, target = comparison.get_relevant_lines(meta, "PLAYLIST REPORT:\nVideo:\nAVC / 25000 kbps\nVideo: AVC / 25000 kbps")
    assert source == comparison.normalize_and_filter(normalized_summary)
    assert target == ["Video: AVC / 25000 kbps"]


def test_compare_bdinfo_differences_matches_and_missing_content(tmp_path: Path) -> None:
    source = "Video: AVC / 25000 kbps\nAudio: DTS / 1500 kbps\nSubtitle: English / 25 kbps"
    meta = _meta(tmp_path, source)

    warning, result = comparison.compare_bdinfo(
        meta,
        {"name": "Different", "bd_info": "Video: AVC / 24000 kbps\nAudio: DTS / 1500 kbps\nSubtitle: French / 30 kbps"},
        "LST",
    )
    assert warning == ""
    assert "+" in result and "-" in result and "Different" in result

    warning, result = comparison.compare_bdinfo(meta, {"name": "Same", "bd_info": source}, "OTHER")
    assert "No differences found" in warning
    assert "⚠" in result

    warning, result = comparison.compare_bdinfo(meta, {"name": "Missing"}, "AITHER")
    assert "No BDInfo found" in warning
    assert "Missing" in result


def test_warning_and_sort_priorities() -> None:
    assert "No BDInfo" in comparison.generate_warning("Release", "", False)
    assert "No differences" in comparison.generate_warning("Release", "content", False)
    assert comparison.generate_warning("Release", "content", True) == ""

    assert comparison.sorting_priority({"content": "23.976 fps"})[0] == 0
    assert comparison.sorting_priority({"content": "Audio DTS"})[0] == 1
    assert comparison.sorting_priority({"content": "Subtitle English"})[0] == 2
    assert comparison.sorting_priority({"content": "Presentation Graphics"})[0] == 2
