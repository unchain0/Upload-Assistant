from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.integrations.trackers.bbcode_formatting import BBCODE


def test_hdb_description_removes_internal_material_and_extracts_external_images() -> (
    None
):
    description = """[center][b]Source vs Encode Comparison[/b]
[url=https://img.hdbits.org/a][img]https://img.hdbits.org/a.jpg[/img][/url]
[/center]
Comparison line
https://hdbits.org/details/1
second line
[url=https://hdbits.org/x][/url]
[url=https://hdbits.org/x]visible[/url]
[img]https://t.hdbits.org/a.png[/img]
https://img.hdbits.org/standalone.jpg
[center][/center]
[url=https://imgbox.com/abc][img]https://thumbs2.imgbox.com/ab/cd/file_t.png[/img][/url]
[url=https://example.com/full][img]https://example.com/full.jpg[/img][/url]
Release notes
"""
    cleaned, images = BBCODE().clean_hdb_description(description)
    assert "hdbits.org" not in cleaned.lower()
    assert cleaned == "Release notes"
    assert images[0]["raw_url"].startswith("https://images2.imgbox.com/")
    assert images[0]["raw_url"].endswith("_o.png")
    assert images[1] == {
        "img_url": "https://example.com/full.jpg",
        "raw_url": "https://example.com/full.jpg",
        "web_url": "https://example.com/full",
    }
    assert BBCODE()._is_hdbits_url("https://sub.hdbits.org/a")
    assert not BBCODE()._is_hdbits_url("https://example.com")


def test_hdb_description_only_bbcode_returns_empty() -> None:
    cleaned, images = BBCODE().clean_hdb_description(
        "[b][/b]\n[center][/center]"
    )
    assert cleaned == "" and images == []


def test_bhd_description_framestor_flux_images_and_empty(
    tmp_path: Path,
) -> None:
    meta = Meta(base_dir=str(tmp_path), uuid="bhd", framestor=True, flux=True)
    description = """[size=4]Header[/size]
[img]https://ignore.example/tag.jpg[/img]
[img=500]https://ignore.example/resized.jpg[/img]
https://images.example/loose.jpg
[URL=https://images.example/loose.jpg][/URL]
Actual notes


"""
    cleaned, images = BBCODE().clean_bhd_description(description, meta)
    assert cleaned.startswith("[code]") and cleaned.endswith("[/code]")
    assert "Actual notes" in cleaned
    assert images == [
        {
            "img_url": "https://images.example/loose.jpg",
            "raw_url": "https://images.example/loose.jpg",
            "web_url": "https://images.example/loose.jpg",
        }
    ]
    assert meta.nfo and meta.bhd_nfo
    assert (tmp_path / "tmp" / "bhd" / "bhd.nfo").is_file()

    empty, _ = BBCODE().clean_bhd_description(
        "[img]https://x.example/a.jpg[/img]",
        Meta(base_dir=str(tmp_path), uuid="empty", flux=True),
    )
    assert empty == ""
    no_flux, _ = BBCODE().clean_bhd_description(
        "text", Meta(base_dir=str(tmp_path), uuid="noflux", flux=False)
    )
    assert no_flux == ""


def test_ptp_description_regular_disc_variants_and_images() -> None:
    bb = BBCODE()
    comparison = """[comparison=Source, Encode]
https://cmp.example/1.jpg
https://cmp.example/2.jpg
https://cmp.example/3.jpg
https://cmp.example/4.jpg
https://cmp.example/5.jpg
https://cmp.example/6.jpg
[/comparison]"""
    hide = """[hide=Comparison Source vs Encode]
[img]https://hide.example/1.jpg[/img][img]https://hide.example/2.jpg[/img][img]https://hide.example/3.jpg[/img]
[img]https://hide.example/4.jpg[/img][img]https://hide.example/5.jpg[/img][img]https://hide.example/6.jpg[/img]
[/hide]"""
    regular = f"""&bull; Notes
[url=https://passthepopcorn.me/torrents.php?id=1]PTP text[/url]
[url=https://hdbits.org/details/1]HDB text[/url]
[mediainfo]remove me[/mediainfo]
General
Complete name : Example

[b]Matroska[/b]
1920x1080
[u]Format:[/u] AVC
2 channels
[quote=Info]quoted[/quote]
[align=center]aligned[/align]
[size=4]sized[/size]
[video]movie[/video][staff]staff[/staff][movie]x[/movie][artist]y[/artist][user]u[/user][indent]i[/indent][hr]
https://loose.example/screen.jpg
{comparison}
{hide}
"""
    cleaned, images = bb.clean_ptp_description(regular, "")
    assert "[code]quoted[/code]" in cleaned
    assert "PTP text" in cleaned and "HDB text" in cleaned
    assert "passthepopcorn.me" not in cleaned and "hdbits.org" not in cleaned
    assert any(
        image["raw_url"] == "https://loose.example/screen.jpg"
        for image in images
    )
    assert "[comparison=" not in cleaned

    dvd, _ = bb.clean_ptp_description(
        "[mediainfo]DVD INFO[/mediainfo]\nDVD notes", "DVD"
    )
    assert "DVD INFO" not in dvd and "DVD notes" in dvd

    bdmv_source = """[mediainfo]x[/mediainfo]
DISC INFO: remove

Disc Title: remove

Disc Size: remove

Protection: remove

BD-Java: remove

BDInfo: remove

PLAYLIST REPORT: remove

Name: remove

Length: remove

Size: remove

Total Bitrate: remove

VIDEO: remove

AUDIO: remove

SUBTITLES: remove

Codec Bitrate Description remove

Codec Language Bitrate Description remove

Keep this
"""
    bdmv, _ = bb.clean_ptp_description(bdmv_source, "BDMV")
    assert "Keep this" in bdmv
    assert "DISC INFO" not in bdmv


def test_ptp_description_source_encode_sections_and_empty() -> None:
    bb = BBCODE()
    source_encode = """Source Vs Encode:
https://source.example/1.jpg
https://source.example/2.jpg
Trailing
"""
    cleaned, images = bb.clean_ptp_description(source_encode, "")
    assert cleaned == "" or "source.example" not in cleaned
    assert images == []
    assert bb.clean_ptp_description("[b][/b]", "")[0] == ""


def test_unit3d_description_site_images_spoilers_centers_and_signatures() -> (
    None
):
    bb = BBCODE()
    desc = """[url=https://tracker.example/torrents/1]Internal text[/url]
[url=https://imgbox.com/abc][img=500]https://images.example/full.jpg[/img][/url]
[img]https://images.example/standalone.png[/img]
[img]https://thumbs.example/thumb.jpg[/img]
[img]https://blutopia.xyz/favicon.ico[/img]
[spoiler=Keep][img]https://inside.example/spoiler.jpg[/img][/spoiler]
[center]   [/center]
[center]  actual center  [/center]
[center][b]Uploaded Using [url=https://github.com/HDInnovations/UNIT3D]UNIT3D[/url] Auto Uploader[/b][/center]
[center][url=https://github.com/z-ink/uploadrr][img=1]https://i.ibb.co/2NVWb0c/uploadrr.webp[/img][/url][/center]
[center][url=https://github.com/edge20200/Only-Uploader]Powered by Only-Uploader[/url][/center]
[center]Created by Upload Assistant[/center]
[right]Created by Upload Assistant[/right]
[right][url=https://github.com/wastaken7/Upload-Assistant]Shared with Upload-Assistant v3.0[/url][/right]
Notes
"""
    cleaned, images = bb.clean_unit3d_description(
        desc, "https://tracker.example"
    )
    assert "Internal text" in cleaned
    assert "Created by" not in cleaned
    assert "Notes" in cleaned
    urls = {image["img_url"] for image in images}
    assert "https://images.example/full.jpg" in urls
    assert "https://images.example/standalone.png" in urls
    assert all(
        "thumbs" not in value and "favicon" not in value for value in urls
    )
    assert "inside.example/spoiler.jpg" in cleaned

    assert (
        bb.clean_unit3d_description(
            "[center][/center]", "https://tracker.example"
        )[0]
        == ""
    )
    assert (
        bb.clean_unit3d_description("[b][/b]", "https://tracker.example")[0]
        == ""
    )


def test_simple_bbcode_transformers() -> None:
    bb = BBCODE()
    assert bb.is_only_bbcode("[b][/b]")
    assert not bb.is_only_bbcode("[b]text[/b]")
    assert bb.convert_pre_to_code("[pre]x[/pre]") == "[code]x[/code]"
    assert bb.convert_code_to_pre("[code]x[/code]") == "[pre]x[/pre]"
    assert (
        bb.convert_hide_to_spoiler("[hide=x]a[/hide]")
        == "[spoiler=x]a[/spoiler]"
    )
    assert (
        bb.convert_spoiler_to_hide("[spoiler=x]a[/spoiler]")
        == "[hide=x]a[/hide]"
    )
    assert bb.remove_hide("[hide=x]a[/hide]") == "a"
    assert (
        bb.convert_named_spoiler_to_named_hide("[spoiler=Name]x[/spoiler]")
        == "[hide=Name]x[/hide]"
    )
    assert bb.remove_spoiler("[spoiler=Name]x[/spoiler]") == "x"
    assert bb.remove_color("[color=red]x[/color]") == "x"
    assert (
        bb.convert_named_spoiler_to_normal_spoiler("[spoiler=Name]x[/spoiler]")
        == "[spoiler]x[/spoiler]"
    )
    assert (
        bb.convert_spoiler_to_code("[spoiler=Name]x[/spoiler]")
        == "[code=Name]x[/code]"
    )
    assert bb.convert_code_to_quote("[code]x[/code]") == "[quote]x[/quote]"
    assert bb.remove_img_resize("[img=500]x[/img]") == "[img]x[/img]"
    assert bb.remove_extra_lines("a\n\n\n\nb") == "a\n\nb"
    assert (
        bb.convert_to_align("[center]x[/center][right]y[/right]")
        == "[align=center]x[/align][align=right]y[/align]"
    )
    assert bb.remove_sup("a[sup]b[/sup]") == "ab"
    assert bb.remove_sub("a[sub]b[/sub]") == "ab"
    assert bb.remove_list("[list][*]a[*]b[/list]") == "[*]a[*]b"


def _comparison() -> str:
    return """[comparison=Source, Encode]
https://img.example/1.jpg
https://img.example/2.jpg
https://img.example/3.jpg
https://img.example/4.jpg
[/comparison]"""


def test_comparison_conversion_paths() -> None:
    bb = BBCODE()
    collapse = bb.convert_comparison_to_collapse(_comparison(), 1000)
    assert "[spoiler=Source vs Encode]" in collapse
    assert "[img=350]" in collapse

    centered = bb.convert_comparison_to_centered(_comparison(), 600)
    assert "[center]Source | Encode" in centered
    assert "[img=300]" in centered

    six_images = """[spoiler=Comparison Source vs Encode]
[img]https://img.example/1.jpg[/img][img]https://img.example/2.jpg[/img][img]https://img.example/3.jpg[/img]
[img]https://img.example/4.jpg[/img][img]https://img.example/5.jpg[/img][img]https://img.example/6.jpg[/img]
[/spoiler]"""
    converted = bb.convert_collapse_to_comparison(
        six_images, "spoiler", [six_images]
    )
    assert "[comparison=Source, Encode]" in converted

    hide = six_images.replace("spoiler", "hide")
    assert "[comparison=Source, Encode]" in bb.convert_collapse_to_comparison(
        hide, "hide", [hide]
    )
    assert bb.convert_collapse_to_comparison("text", "spoiler", []) == "text"


def test_collapse_conversion_skips_invalid_or_small_groups() -> None:
    bb = BBCODE()
    small = (
        "[spoiler=Comparison]"
        + "".join(f"[img]https://x/{i}.jpg[/img]" for i in range(3))
        + "[/spoiler]"
    )
    assert (
        bb.convert_collapse_to_comparison(small, "spoiler", [small]) == small
    )
    invalid = "".join(f"[img]https://x/{i}.jpg[/img]" for i in range(6))
    assert (
        bb.convert_collapse_to_comparison(invalid, "spoiler", [invalid])
        == invalid
    )
    assert (
        bb.convert_collapse_to_comparison(invalid, "hide", [invalid])
        == invalid
    )


def test_ptp_generic_comparison_placeholder_roundtrip() -> None:
    bb = BBCODE()
    comp = """[comparison=A, B]
https://cmp.example/a.jpg
https://cmp.example/b.jpg
[/comparison]
notes"""
    cleaned, _images = bb.clean_ptp_description(comp, "")
    assert "[comparison=A, B]" in cleaned
    assert "notes" in cleaned


def test_centered_comparison_caps_width_and_empty_collapse_sources() -> None:
    bb = BBCODE()
    centered = bb.convert_comparison_to_centered(_comparison(), 1000)
    assert "[img=350]" in centered
    empty_sources = (
        "[spoiler=]"
        + "".join(
            f"[img]https://x.example/{index}.jpg[/img]" for index in range(6)
        )
        + "[/spoiler]"
    )
    assert (
        bb.convert_collapse_to_comparison(
            empty_sources, "spoiler", [empty_sources]
        )
        == empty_sources
    )
