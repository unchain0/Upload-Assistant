# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

import os
import random
from functools import partial
from pathlib import Path
from typing import Any, cast

import awsmfunc as awsmfunc  # pyright: ignore[reportMissingImports] # pyrefly: ignore [missing-import]
import vapoursynth as vs  # pyright: ignore[reportMissingImports] # pyrefly: ignore [missing-import]

from src.integrations.observability.runtime_support import logger

vs = cast(Any, vs)  # pyright: ignore[reportUnnecessaryCast]
awsmfunc = cast(Any, awsmfunc)  # pyright: ignore[reportUnnecessaryCast]
core: Any = vs.core
DynamicTonemap: Any = awsmfunc.DynamicTonemap
ScreenGen: Any = awsmfunc.ScreenGen
zresize: Any = awsmfunc.zresize

# core.std.LoadPlugin(path="/usr/local/lib/vapoursynth/libffms2.so")
# core.std.LoadPlugin(path="/usr/local/lib/vapoursynth/libsub.so")
# core.std.LoadPlugin(path="/usr/local/lib/vapoursynth/libimwri.so")


def custom_frame_info(clip: Any, _text: str) -> Any:
    def frame_props(n: int, f: Any, clip: Any) -> Any:
        # Modify the frame properties extraction here to avoid the decode issue
        info = f"Frame {n} of {clip.num_frames}\nPicture type: {f.props['_PictType']}"
        # Adding the frame information as text to the clip
        return core.text.Text(clip, info)

    # Apply frame_props to each frame
    return core.std.FrameEval(
        clip, partial(frame_props, clip=clip), prop_src=clip
    )


def _image_optimization_requested(
    image_path: Path, config: dict[str, Any]
) -> bool:
    return bool(config.get("optimize_images", True) and image_path.exists())


def _load_oxipng() -> Any | None:
    import platform

    pyver = platform.python_version_tuple()
    if int(pyver[0]) != 3 or int(pyver[1]) < 7:
        return None
    import oxipng  # pyright: ignore[reportMissingImports] # pyrefly: ignore [missing-import]

    return oxipng


def _optimization_level(image_path: Path) -> int:
    return 6 if image_path.stat().st_size >= 16_000_000 else 3


def optimize_images(image: str | Path, config: dict[str, Any]) -> None:
    image_path = Path(image)
    if not _image_optimization_requested(image_path, config):
        return
    try:
        oxipng = _load_oxipng()
        if oxipng is None:
            return
        oxipng.optimize(image, level=_optimization_level(image_path))
    except Exception as error:
        logger.info(
            f"Image optimization failed: {error}", extra={"markup": False}
        )


def _screen_frames_valid(frames: list[int], num: int) -> bool:
    return len(frames) == num and all(frame >= 0 for frame in frames)


def _existing_screen_frames(screens_file: Path, num: int) -> list[int]:
    if not screens_file.exists():
        return []
    with screens_file.open() as text_file:
        frames = [int(line.strip()) for line in text_file.readlines()]
    if not _screen_frames_valid(frames, num):
        return []
    logger.info(
        f"Using existing frame numbers from {screens_file}",
        extra={"markup": False},
    )
    return frames


def _ffms_cache_file(directory: str) -> str:
    return f"{Path(directory).resolve()!s}{os.sep}ffms2.ffms2"


def _index_ffms_source(source: str, directory: str) -> Any:
    cachefile = _ffms_cache_file(directory)
    if not Path(cachefile).exists():
        logger.info(
            f"Indexing {source} with ffms2... This may take a while.",
            extra={"markup": False},
        )
    try:
        src = core.ffms2.Source(source, cachefile=cachefile)
    except Exception as error:
        logger.info(
            f"Error during indexing: {error!s}", extra={"markup": False}
        )
        raise
    if Path(cachefile).exists():
        logger.info(
            f"Indexing completed and cached at: {cachefile}",
            extra={"markup": False},
        )
    else:
        logger.info(
            "Indexing did not complete as expected.",
            extra={"markup": False},
        )
    return src


def _index_source(source: str, directory: str) -> Any:
    if not source.endswith(".m2ts"):
        return _index_ffms_source(source, directory)
    logger.info(
        f"Indexing {source} with LSMASHSource... This may take a while.",
        extra={"markup": False},
    )
    return core.lsmas.LWLibavSource(source)


def _encode_clip(encode: str | None) -> tuple[str | None, Any | None]:
    if not encode:
        return None, None
    if Path(encode).exists():
        return encode, core.ffms2.Source(encode)
    logger.info(
        f"Encode file {encode} not found. Skipping encode processing.",
        extra={"markup": False},
    )
    return None, None


def _write_random_frames(src: Any, num: int, screens_file: Path) -> list[int]:
    start, end = 1000, len(src) - 10000
    frames = sorted(
        random.randint(start, end)  # nosec B311  # noqa: S311
        for _ in range(num)
    )
    with screens_file.open("w") as text_file:
        text_file.writelines(f"{frame}\n" for frame in frames)
    logger.info(
        f"Generated and saved new frame numbers to {screens_file}",
        extra={"markup": False},
    )
    return frames


def _resize_dimensions(enc: Any) -> tuple[int | None, int | None]:
    if enc.width / enc.height > 16 / 9:
        return enc.width, None
    return None, enc.height


def _resize_source_for_encode(src: Any, enc: Any | None) -> Any:
    if enc is None or (src.width == enc.width and src.height == enc.height):
        return src
    ref = zresize(enc, preset=src.height)
    horizontal_crop = (src.width - ref.width) / 2
    vertical_crop = (src.height - ref.height) / 2
    src = src.std.Crop(
        left=horizontal_crop,
        right=horizontal_crop,
        top=vertical_crop,
        bottom=vertical_crop,
    )
    width, height = _resize_dimensions(enc)
    return zresize(src, width=width, height=height)


def _tonemap_clips(src: Any, enc: Any | None) -> tuple[Any, Any | None, bool]:
    frame = src.get_frame(0)
    if frame.props["_Primaries"] != 9:
        return src, enc, False
    src = DynamicTonemap(
        src, src_fmt=False, libplacebo=True, adjust_gamma=True
    )
    if enc is not None:
        enc = DynamicTonemap(
            enc, src_fmt=False, libplacebo=True, adjust_gamma=True
        )
    return src, enc, True


def _render_screens(
    src: Any, enc: Any | None, encode: str | None, directory: str
) -> None:
    ScreenGen(src, directory, "a")
    if encode and enc is not None:
        annotated = custom_frame_info(enc, "Encode (Tonemapped)")
        ScreenGen(annotated, directory, "b")


def _optimize_screens(
    directory: str, num: int, config: dict[str, Any]
) -> None:
    for index in range(1, num + 1):
        image_path = Path(directory) / f"{str(index).zfill(2)}a.png"
        optimize_images(image_path, config)


def vs_screengn(
    source: str,
    encode: str | None = None,
    num: int = 5,
    dir: str = ".",
    config: dict[str, Any] | None = None,
) -> None:
    selected_config = {"optimize_images": True} if config is None else config
    screens_file = Path(dir) / "screens.txt"
    existing_frames = _existing_screen_frames(screens_file, num)
    src = _index_source(source, dir)
    encode, enc = _encode_clip(encode)
    if not existing_frames:
        _write_random_frames(src, num, screens_file)
    src = _resize_source_for_encode(src, enc)
    src, enc, tonemapped = _tonemap_clips(src, enc)
    if tonemapped:
        src = custom_frame_info(src, "Tonemapped")
    _render_screens(src, enc, encode, dir)
    _optimize_screens(dir, num, selected_config)
