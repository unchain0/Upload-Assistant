from src.integrations.trackers.bbcode_formatting import (
    BBCODE,
    RELEASE_GROUP_BOILERPLATE,
    UPLOAD_TOOL_SIGNATURES,
    _block,
)


def test_clean_unit3d_description_removes_line_wrapped_align_center_signature() -> (
    None
):
    description = """Release notes
[align=center]
[url=https://github.com/wastaken7/Upload-Assistant]
[size=4]
Shared with Upload-Assistant v3.3 (fork)
[/size]
[/url]
[/align]"""

    cleaned, _ = BBCODE().clean_unit3d_description(
        description, "https://example.test"
    )

    assert cleaned == "Release notes"


def test_clean_unit3d_description_preserves_non_version_suffix() -> None:
    description = "[right][url=https://github.com/wastaken7/Upload-Assistant]Shared with Upload Assistant release[/url][/right]"

    cleaned, _ = BBCODE().clean_unit3d_description(
        description, "https://example.test"
    )

    assert cleaned == description


def test_release_group_boilerplate_is_removed_before_images_are_extracted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.integrations.trackers.bbcode_formatting.RELEASE_GROUP_BOILERPLATE",
        {
            "FictionalGroup": (
                r"\[img[^\]]*\][^\[]*\[/img\]\s*(?=~~ Shots ~~)",
                r"~~ Shots ~~",
                r"More from FictionalGroup",
            )
        },
    )
    desc = (
        "[center][img=500]https://img.example/banner.png[/img]\n"
        "~~ Shots ~~[/center]\n"
        "Fictional notes.\n"
        "[center][url=https://img.example/s1][img=500]https://img.example/s1.png[/img][/url][/center]\n"
        "[center]More from FictionalGroup[/center]"
    )

    cleaned, images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/api/torrents/1"
    )

    assert cleaned.strip() == "Fictional notes."
    assert [image["img_url"] for image in images] == [
        "https://img.example/s1.png"
    ]


def test_kitsune_divider_and_banner_are_removed() -> None:
    desc = (
        "[h3][center][img=500]https://img.example/banner.png[/img]\n"
        "    •❅───✧❅✦ [color=#F69047]Screenshots[/color] ✦❅✧───❅•[/center]\n"
        "[center][url=https://img.example/s1][img=500]https://img.example/s1.png[/img][/url][/center][/h3]"
    )

    cleaned, images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/api/torrents/1"
    )

    assert cleaned == ""
    assert [image["img_url"] for image in images] == [
        "https://img.example/s1.png"
    ]


def test_clean_unit3d_description_keeps_site_subdomain_image_hosts() -> None:
    desc = (
        "Notes about unit3d.example.\n"
        "[url=https://img.example/s1][img]https://files.unit3d.example/u/s1.png[/img][/url]"
    )

    cleaned, images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/api/torrents/1"
    )

    assert cleaned == "Notes about unit3d."
    assert images[0]["img_url"] == "https://files.unit3d.example/u/s1.png"


def test_clean_unit3d_description_removes_signature_of_a_renamed_fork() -> (
    None
):
    desc = (
        "Fictional notes.\n"
        "[right][url=https://github.com/fictional-user/fictional-upload-assistant]"
        "[size=4]Shared with Upload-Assistant v1.2 (fork)[/size][/url][/right]"
    )

    cleaned, _images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/api/torrents/1"
    )

    assert cleaned == "Fictional notes."


def test_clean_unit3d_description_keeps_links_to_other_github_projects() -> (
    None
):
    desc = (
        "[right][url=https://github.com/fictional-user/fictional-tool]"
        "[size=4]Shared with Upload-Assistant v1.2[/size][/url][/right]"
    )

    cleaned, _images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/api/torrents/1"
    )

    assert cleaned == desc


def test_known_boilerplate_patterns_compile() -> None:
    import re

    for patterns in (
        *RELEASE_GROUP_BOILERPLATE.values(),
        *UPLOAD_TOOL_SIGNATURES.values(),
    ):
        for pattern in patterns:
            re.compile(pattern)


def _clean_unit3d(desc: str) -> tuple[str, list[str]]:
    cleaned, images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/api/torrents/1"
    )
    return cleaned.strip(), [image["img_url"] for image in images]


SHOT = "[url=https://img.example/s1][img=350]https://img.example/s1.png[/img][/url]"


def test_upload_tool_signature_in_its_own_block_is_removed() -> None:
    desc = (
        f"Fictional notes.\n[center]{SHOT}[/center]\n"
        "[right][url=https://github.com/autobrr/upbrr]"
        "[size=4]Uploaded by upbrr[/size][/url][/right]"
    )

    assert _clean_unit3d(desc) == (
        "Fictional notes.",
        ["https://img.example/s1.png"],
    )


def test_upload_tool_signature_sharing_a_block_with_screenshots_keeps_the_screenshots() -> (
    None
):
    desc = (
        "Fictional notes.\n"
        f"[center][color=#7760de][size=16]⚡ Uploaded using EASY UPLOAD3R ⚡[/size][/color]\n"
        f"{SHOT}[/center]"
    )

    assert _clean_unit3d(desc) == (
        "Fictional notes.",
        ["https://img.example/s1.png"],
    )


def test_upload_tool_text_and_image_signatures_are_removed() -> None:
    desc = (
        "[center][b][i][url=https://codeberg.org/CvT/Uploadrr]"
        "Powered by Uploadrr[/url][/i][/b][/center]\n"
        f"Fictional notes.\n[center]{SHOT}[/center]\n"
        "[center][url=https://codeberg.org/CvT/Uploadrr]"
        "[img=400]https://img.example/tool-logo.webp[/img][/url][/center]"
    )

    assert _clean_unit3d(desc) == (
        "Fictional notes.",
        ["https://img.example/s1.png"],
    )


def test_nfoforge_signature_is_removed_and_the_nfo_kept() -> None:
    desc = (
        "[code]Fictional release notes.\n\n"
        "[size=15]Shared with [url=https://github.com/jesterr0/NfoForge]"
        "NfoForge v1.0.0[/url][/size]\n[/code]"
    )

    cleaned, _images = _clean_unit3d(desc)

    assert "NfoForge" not in cleaned
    assert "Fictional release notes." in cleaned


def test_upsies_and_ak_automated_uploader_signatures_are_removed() -> None:
    desc = (
        f"Fictional notes.\n[center]{SHOT}[/center]\n"
        "[right][size=1]Shared with [url=https://upsies.readthedocs.io]"
        "upsies[/url][/size][/right]\n"
        "[center][small]Uploaded with "
        "[url=https://github.com/AQTKU/ak-automated-uploader]"
        "AK Automated Uploader[/url][/small][/center]"
    )

    assert _clean_unit3d(desc) == (
        "Fictional notes.",
        ["https://img.example/s1.png"],
    )


def test_only_uploader_and_magus_signatures_are_removed() -> None:
    desc = (
        f"Fictional notes.\n[center]{SHOT}[/center]\n"
        "[center][url=https://github.com/edge20200/Only-Uploader]"
        "Brought to you by Only-Uploader[/url][/center]"
        "[right][size=4][color=#4DA6FF]"
        "[url=https://dailyusetools.com/MAGUS]"
        "Uploaded with MAGUS v1.0.0[/url][/color][/size][/right]"
    )

    assert _clean_unit3d(desc) == (
        "Fictional notes.",
        ["https://img.example/s1.png"],
    )


def test_block_patterns_only_remove_whole_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.integrations.trackers.bbcode_formatting.RELEASE_GROUP_BOILERPLATE",
        {"FictionalGroup": (_block(r"SCREENSHOTS"),)},
    )
    desc = (
        "[center][color=#a61805][size=18][font=Arial]SCREENSHOTS"
        "[/font][/size][/color][/center]\n"
        "The SCREENSHOTS below are fictional."
    )

    assert _clean_unit3d(desc)[0] == "The SCREENSHOTS below are fictional."


def test_upload_tool_signature_does_not_swallow_a_screenshot_in_the_same_block() -> (
    None
):
    desc = (
        "Fictional notes.\n"
        "[center][url=https://github.com/autobrr/upbrr]"
        f"[size=4]Uploaded by upbrr[/size][/url]\n{SHOT}[/center]"
    )

    assert _clean_unit3d(desc) == (
        "Fictional notes.",
        ["https://img.example/s1.png"],
    )


def test_tmdb_artwork_is_not_extracted_as_a_screenshot() -> None:
    desc = (
        "Notes.\n"
        "[center][img=300]https://image.tmdb.org/t/p/original/fictional-logo.png"
        "[/img][/center]\n"
        f"[center]{SHOT}[/center]"
    )

    _cleaned, images = BBCODE().clean_unit3d_description(
        desc, "https://unit3d.example/torrents/1"
    )

    assert [image["img_url"] for image in images] == [
        "https://img.example/s1.png"
    ]
