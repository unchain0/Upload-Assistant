from io import BytesIO
from pathlib import Path

from PIL import Image
from resvg_py import svg_to_bytes

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE_SVG = BASE_DIR / "docs" / "assets" / "logo.svg"


def draw_logo(size: int = 512, svg_path: Path = SOURCE_SVG) -> Image.Image:
    """Render the canonical repository logo SVG as an RGBA Pillow image."""

    if not svg_path.is_file():
        raise FileNotFoundError(f"Logo source not found: {svg_path}")
    rendered_svg = svg_to_bytes(svg_path=str(svg_path), width=size, height=size)
    return Image.open(BytesIO(rendered_svg)).convert("RGBA")


def main() -> None:
    """Regenerate documentation and Windows-installer raster assets."""

    docs_assets = BASE_DIR / "docs" / "assets"
    scripts_dir = BASE_DIR / "scripts"
    docs_assets.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    logo = draw_logo(512)
    logo.save(docs_assets / "logo.png", "PNG")
    logo.save(scripts_dir / "logo.ico", format="ICO", sizes=[(size, size) for size in (16, 32, 48, 64, 128, 256)])
    print("Regenerated docs/assets/logo.png and scripts/logo.ico")


if __name__ == "__main__":
    main()
