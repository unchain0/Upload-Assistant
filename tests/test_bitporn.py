import asyncio
from pathlib import Path

from src.domain_models.release import Meta
from src.integrations.filesystem.screenshot_manifest import (
    register as register_screenshots,
)
from src.integrations.trackers.registry import tracker_class_map
from src.integrations.trackers.UNIT3D.bitporn import BitPorn


def tracker() -> BitPorn:
    return BitPorn({"DEFAULT": {}, "TRACKERS": {"BITPORN": {}}})


def test_bitporn_is_registered_for_xxx_only() -> None:
    assert tracker_class_map["BITPORN"] is BitPorn
    assert tracker().supported_categories == ("XXX",)


def test_bitporn_infers_category_from_basename_only() -> None:
    bitporn = tracker()

    assert asyncio.run(
        bitporn.get_category_id(
            Meta(
                category="XXX",
                basename_no_ext="OnlyFans.2026.Creator.Big.Tits.1080p",
            )
        )
    ) == {"category_id": "10"}
    assert asyncio.run(
        bitporn.get_category_id(
            Meta(category="XXX", basename_no_ext="ManyVids.Creator.1080p")
        )
    ) == {"category_id": "20"}
    assert asyncio.run(
        bitporn.get_category_id(
            Meta(category="XXX", basename_no_ext="Plain.Release.1080p")
        )
    ) == {"category_id": "52"}


def test_bitporn_category_mappings_and_no_type_field() -> None:
    bitporn = tracker()
    meta = Meta(category="XXX", basename_no_ext="Release")

    assert (
        asyncio.run(bitporn.get_category_id(meta, mapping_only=True))[
            "Ai Generated"
        ]
        == "54"
    )
    assert (
        asyncio.run(bitporn.get_category_id(meta, reverse=True))["52"]
        == "Uncategorized"
    )
    assert asyncio.run(bitporn.get_category_id(meta, category="Anal")) == {
        "category_id": "5"
    }
    assert asyncio.run(bitporn.get_category_id(meta, category="Unknown")) == {
        "category_id": "52"
    }
    assert asyncio.run(bitporn.get_type_id(meta)) == {"type_id": "1"}


def test_bitporn_resolution_mapping() -> None:
    bitporn = tracker()
    meta = Meta(category="XXX", resolution="2160p")

    assert asyncio.run(bitporn.get_resolution_id(meta)) == {
        "resolution_id": "18"
    }
    assert asyncio.run(bitporn.get_resolution_id(meta, "2048p")) == {
        "resolution_id": "14"
    }
    assert asyncio.run(bitporn.get_resolution_id(meta, "1080i")) == {
        "resolution_id": "11"
    }
    assert (
        asyncio.run(bitporn.get_resolution_id(meta, mapping_only=True))[
            "2160p"
        ]
        == "18"
    )
    assert asyncio.run(bitporn.get_resolution_id(meta, reverse=True))[
        "18"
    ] == ("2160p")


def test_bitporn_uses_its_image_upload_contract(tmp_path: Path) -> None:
    meta = Meta(category="XXX", base_dir=str(tmp_path), uuid="bitporn-images")
    screenshot_dir = tmp_path / "tmp" / meta.uuid / "screenshots"
    screenshot_dir.mkdir(parents=True)
    source = screenshot_dir / "contact-sheet.png"
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0dIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (tmp_path / "tmp" / meta.uuid / "MEDIAINFO_CLEANPATH.txt").write_text(
        "MediaInfo", encoding="utf-8"
    )
    registered = register_screenshots(
        meta.base_dir, meta.uuid, [source], "main"
    )
    meta.artwork_path = str(registered[0])
    meta.artwork_banner_path = str(registered[0])

    bitporn = tracker()
    files = asyncio.run(bitporn.get_additional_files(meta))
    data = asyncio.run(bitporn.get_data(meta))

    assert "description_images[0]" in files
    assert files["description_images[0]"][2] == "image/png"
    assert "cover" in files
    assert "banner" in files
    assert "torrent-cover" not in files
    assert "torrent-banner" not in files
    assert "[upimg1]" in data["description"]
    assert data["description_image_widths[1]"] == "450"
