from __future__ import annotations

import asyncio
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.media import screenshot_capture as capture


def _image_bytes(path: Path, *, fmt: str = "PNG") -> bytes:
    Image.new("RGB", (4, 4), "white").save(path, fmt)
    return path.read_bytes()


def test_config_rounding_host_and_manager_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    capture._apply_config(
        {
            "DEFAULT": {
                "process_limit": "bad",
                "cutoff_screens": object(),
                "desat": "bad",
                "xxx_contact_sheet_rows": object(),
                "xxx_contact_sheet_animation_seconds": object(),
                "scale_screenshots_for_par": "yes",
            }
        }
    )
    assert capture.task_limit == 1 and capture.cutoff == 1 and capture.desat == 10.0
    assert capture.xxx_contact_sheet_settings()[0] == 12
    assert capture.xxx_contact_sheet_animation_settings() == (False, 5.0)
    assert capture.round_to_even(3.0) == 4
    assert capture.round_to_even(4.0) == 4
    assert capture.screenshot_par_scale_factors(720, 576, 0.8, 4 / 3, True)[1] != 1
    assert capture.screenshot_par_scale_factors(720, 576, 1.2, 4 / 3, True) == (1.2, 1.0)

    assert asyncio.run(capture.get_image_host(Meta(imghost=[None, 7, " ImgBox "]))) == "imgbox"
    assert asyncio.run(capture.get_image_host(Meta(imghost=[]))) is None
    capture.default_config = {"img_host_10": "ignored", "img_host_1": "PixHost"}
    assert asyncio.run(capture.get_image_host(Meta(imghost=None))) == "pixhost"

    manager = capture.TakeScreensManager({"DEFAULT": {"xxx_contact_sheet_rows": 2}})
    monkeypatch.setattr(capture, "sanitize_filename", AsyncMock(return_value="safe"))
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1"]))
    assert asyncio.run(manager.sanitize_filename("bad")) == "safe"
    assert asyncio.run(manager.get_image_host(Meta())) == "imgbb"
    assert asyncio.run(manager.valid_ss_time([], 1, 10, 24, Meta())) == ["1"]
    assert manager.xxx_contact_sheet_settings()[0] == 2


def test_contact_sheet_empty_existing_retake_cached_failure_and_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = Meta(base_dir=str(tmp_path), uuid="xxx", category="XXX", screens=1)
    assert asyncio.run(capture.xxx_contact_sheets([], meta.uuid, meta.base_dir, meta)) == []
    assert meta.screens == 0

    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    screenshot_dir = capture.screenshots_dir(meta.base_dir, meta.uuid)
    cached = screenshot_dir / "cached.png"
    cached.write_bytes(b"image")
    capture.register_screenshots(meta.base_dir, meta.uuid, [str(cached)], "main")
    meta.retake = False
    result = asyncio.run(capture.xxx_contact_sheets([str(video)], meta.uuid, meta.base_dir, meta))
    assert len(result) == 1 and meta.screens == 1

    meta.retake = True
    monkeypatch.setattr(capture, "_xxx_contact_sheet_fontfile", lambda: None)
    monkeypatch.setattr(capture.ffmpeg, "probe", lambda _path: {"format": {"duration": "0"}})
    assert asyncio.run(capture.xxx_contact_sheets([str(video)], meta.uuid, meta.base_dir, meta)) == []

    meta.retake = False
    existing = screenshot_dir / "xxx-contact-sheet-1.png"
    existing.write_bytes(b"image")
    capture.clear_screenshot_group(meta.base_dir, meta.uuid, "main")
    result = asyncio.run(capture.xxx_contact_sheets([str(video)], meta.uuid, meta.base_dir, meta))
    assert len(result) == 1 and Path(result[0]).is_file()
    capture.clear_screenshot_group(meta.base_dir, meta.uuid, "main")

    existing.unlink(missing_ok=True)
    monkeypatch.setattr(capture.ffmpeg, "probe", lambda _path: {"format": {"duration": "60"}})
    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(1, b"", b"failed")))
    assert asyncio.run(capture.xxx_contact_sheets([str(video)], meta.uuid, meta.base_dir, meta)) == []

    monkeypatch.setattr(capture.ffmpeg, "probe", lambda _path: (_ for _ in ()).throw(RuntimeError("probe failed")))
    assert asyncio.run(capture.xxx_contact_sheets([str(video)], meta.uuid, meta.base_dir, meta)) == []


def test_discard_smallest_missing_and_empty(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"
    assert capture.discard_smallest_capture_result([str(missing)]) is None
    assert capture.discard_smallest_capture_result([]) is None


class _OwnedProcess:
    def __init__(self, *, timeout_then_exit: bool = False) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.timeout_then_exit = timeout_then_exit
        self.wait_calls = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        raise asyncio.CancelledError

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.timeout_then_exit and self.wait_calls == 1:
            await asyncio.Event().wait()
        self.returncode = self.returncode if self.returncode is not None else -15
        return self.returncode


@pytest.mark.asyncio
async def test_run_ffmpeg_linux_arch_bundled_pipe_and_cancel_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    class Command:
        node = SimpleNamespace(kwargs={"filename": "pipe:"}, incoming_edges=[])

        def compile(self) -> list[str]:
            return ["ffmpeg", "-i", "input", "pipe:"]

    process = _OwnedProcess(timeout_then_exit=True)
    monkeypatch.setattr(capture, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture.platform, "system", lambda: "Linux")
    monkeypatch.setattr(capture.platform, "machine", lambda: "mips")
    monkeypatch.setattr(capture.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    async def timeout_wait(awaitable: object, **_kwargs: object) -> object:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(capture.asyncio, "wait_for", timeout_wait)

    task = asyncio.create_task(capture.run_ffmpeg(Command()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated and process.killed

    class Edge:
        upstream_node = None

    class FallbackCommand:
        node = SimpleNamespace(kwargs={}, incoming_edges=[Edge()])

        def compile(self) -> list[str]:
            return ["ffmpeg", "output.png"]

    assert capture.get_ffmpeg_output_path(FallbackCommand(), ["ffmpeg", "output.png"]) == "output.png"


def test_audiobook_cover_all_container_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mutagen

    output = tmp_path / "cover.bin"
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")

    assert not asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(path=str(tmp_path / "missing")), str(output)))

    class Picture:
        def __init__(self, data: bytes, type_: int) -> None:
            self.data = data
            self.type = type_

    monkeypatch.setattr(mutagen, "File", lambda _path: SimpleNamespace(pictures=[Picture(b"back", 4), Picture(b"front", 3)], tags={}))
    assert asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio)]), str(output), confirmed_only=True))
    assert output.read_bytes() == b"front"

    monkeypatch.setattr(mutagen, "File", lambda _path: SimpleNamespace(pictures=[Picture(b"first", 4)], tags={}))
    assert asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio)]), str(output)))
    assert output.read_bytes() == b"first"

    class APIC:
        def __init__(self, data: bytes, type_: int) -> None:
            self.data = data
            self.type = type_

    monkeypatch.setattr(mutagen, "File", lambda _path: SimpleNamespace(pictures=[], tags={"APIC:one": APIC(b"fallback", 4), "APIC:front": APIC(b"front", 3)}))
    assert asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio)]), str(output), confirmed_only=True))
    assert output.read_bytes() == b"front"

    monkeypatch.setattr(mutagen, "File", lambda _path: SimpleNamespace(pictures=[], tags={"APIC:one": APIC(b"fallback", 4)}))
    assert asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio)]), str(output)))
    assert output.read_bytes() == b"fallback"

    class MP4:
        pictures: ClassVar[list[Any]] = []
        tags: ClassVar[dict[str, Any]] = {}

        def __contains__(self, key: object) -> bool:
            return key == "covr"

        def __getitem__(self, key: str) -> list[bytes]:
            if key == "covr":
                return [b"mp4-cover"]
            raise KeyError(key)

    monkeypatch.setattr(mutagen, "File", lambda _path: MP4())
    assert asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio)]), str(output)))
    assert output.read_bytes() == b"mp4-cover"

    monkeypatch.setattr(mutagen, "File", lambda _path: None)
    assert not asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio), "other.txt"]), str(output)))

    async def fail_thread(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("thread failed")

    monkeypatch.setattr(capture.asyncio, "to_thread", fail_thread)
    assert not asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio)]), str(output)))


def _write_epub(path: Path, files: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)


@pytest.mark.asyncio
async def test_epub_cover_manifest_html_svg_case_fallbacks(tmp_path: Path) -> None:
    output = tmp_path / "cover.bin"
    direct = tmp_path / "direct.epub"
    _write_epub(
        direct,
        {
            "META-INF/container.xml": '<container><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>',
            "OPS/content.opf": '<package><manifest><item id="img" href="images/cover.png" media-type="image/png" properties="cover-image"/></manifest></package>',
            "OPS/images/cover.png": b"direct",
        },
    )
    assert await capture.extract_epub_cover(str(direct), str(output), confirmed_only=True)
    assert output.read_bytes() == b"direct"

    html = tmp_path / "html.epub"
    _write_epub(
        html,
        {
            "content.opf": '<package><metadata><meta name="cover" content="cover-page"/></metadata><manifest><item id="cover-page" href="Cover.XHTML" media-type="application/xhtml+xml"/></manifest></package>',
            "cover.xhtml": '<html><body><img src="images%2Ffront.jpg"/></body></html>',
            "images/front.jpg": b"html-cover",
        },
    )
    assert await capture.extract_epub_cover(str(html), str(output), confirmed_only=True)
    assert output.read_bytes() == b"html-cover"

    svg = tmp_path / "svg.epub"
    _write_epub(
        svg,
        {
            "book.opf": '<package><manifest><item id="cover" href="cover.html" media-type="text/html"/></manifest></package>',
            "cover.html": '<svg><image xlink:href="../images/front.svg"/></svg>',
            "images/front.svg": b"svg-cover",
        },
    )
    assert await capture.extract_epub_cover(str(svg), str(output))
    assert output.read_bytes() == b"svg-cover"

    named = tmp_path / "named.epub"
    _write_epub(
        named,
        {
            "book.opf": '<package><manifest><item id="art" href="images/not-cover.png" media-type="image/png"/></manifest></package>',
            "images/not-cover.png": b"named",
        },
    )
    assert await capture.extract_epub_cover(str(named), str(output))

    zip_cover = tmp_path / "zip-cover.epub"
    _write_epub(zip_cover, {"book.opf": "<package/>", "assets/COVER.JPG": b"zip-cover"})
    assert await capture.extract_epub_cover(str(zip_cover), str(output))

    first = tmp_path / "first.epub"
    _write_epub(first, {"book.opf": '<package><manifest><item id="one" href="a.png" media-type="image/png"/></manifest></package>', "a.png": b"first"})
    assert await capture.extract_epub_cover(str(first), str(output))

    no_cover = tmp_path / "none.epub"
    _write_epub(no_cover, {"book.opf": "<package/>"})
    assert not await capture.extract_epub_cover(str(no_cover), str(output), confirmed_only=True)
    assert not await capture.extract_epub_cover(str(tmp_path / "invalid.epub"), str(output))


def test_document_cover_pdf_cbz_cbr_and_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fitz

    output = tmp_path / "cover.png"
    assert not asyncio.run(capture.extract_document_cover(str(tmp_path / "book.txt"), str(output)))

    pdf = tmp_path / "book.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    assert asyncio.run(capture.extract_document_cover(str(pdf), str(output)))
    assert output.is_file()

    original_open = fitz.open

    class EmptyDocument:
        def __enter__(self) -> EmptyDocument:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __len__(self) -> int:
            return 0

    monkeypatch.setattr(fitz, "open", lambda *_args, **_kwargs: EmptyDocument())
    assert not asyncio.run(capture.extract_document_cover(str(pdf), str(output)))

    monkeypatch.setattr(fitz, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pdf failed")))
    assert not asyncio.run(capture.extract_document_cover(str(pdf), str(output)))
    monkeypatch.setattr(fitz, "open", original_open)

    png = tmp_path / "page1.png"
    jpg = tmp_path / "page2.jpg"
    _image_bytes(png)
    _image_bytes(jpg, fmt="JPEG")
    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as archive:
        archive.write(jpg, "page10.jpg")
        archive.write(png, "page2.png")
    assert asyncio.run(capture.extract_document_cover(str(cbz), str(output)))
    assert output.is_file()

    no_images = tmp_path / "empty.cbz"
    _write_epub(no_images, {"readme.txt": "none"})
    assert not asyncio.run(capture.extract_document_cover(str(no_images), str(output)))

    broken = tmp_path / "broken.cbr"
    broken.write_bytes(b"bad")
    assert not asyncio.run(capture.extract_document_cover(str(broken), str(output)))


def test_prepare_book_cover_all_priorities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.epub"
    source.write_bytes(b"book")
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"image")
    monkeypatch.setattr(capture, "is_valid_cover_image", lambda path: str(path) == str(existing))
    meta = Meta(artwork_path=str(existing), retake=False)
    assert asyncio.run(capture.prepare_book_cover(str(source), "book", str(tmp_path), meta)) == str(existing)

    output = capture.artwork_dir(str(tmp_path), "book") / "POSTER.png"
    output.write_bytes(b"image")
    monkeypatch.setattr(capture, "is_valid_cover_image", lambda path: str(path) == str(output))
    meta = Meta(artwork_path="", retake=False)
    assert asyncio.run(capture.prepare_book_cover(str(source), "book", str(tmp_path), meta)) == str(output)

    monkeypatch.setattr(capture, "is_valid_cover_image", lambda _path: False)
    for confirmed, downloaded, unconfirmed, expected in (
        (True, False, False, True),
        (False, True, False, True),
        (False, False, True, True),
        (False, False, False, False),
    ):
        monkeypatch.setattr(capture, "extract_embedded_cover_from_audiobook", AsyncMock(side_effect=[confirmed, unconfirmed] if not confirmed else [True]))
        monkeypatch.setattr(capture, "download_artwork_from_meta", AsyncMock(return_value=downloaded))
        meta = Meta(audiobook=True, artwork_path="", retake=False)
        result = asyncio.run(capture.prepare_book_cover(str(source), "audio", str(tmp_path), meta))
        assert bool(result) is expected

    monkeypatch.setattr(capture, "extract_epub_cover", AsyncMock(side_effect=[True]))
    meta = Meta(audiobook=False, artwork_path="", retake=False)
    assert asyncio.run(capture.prepare_book_cover(str(source), "epub", str(tmp_path), meta))

    monkeypatch.setattr(capture, "extract_epub_cover", AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(capture, "download_artwork_from_meta", AsyncMock(return_value=False))
    meta = Meta(audiobook=False, artwork_path="", retake=False)
    assert asyncio.run(capture.prepare_book_cover(str(source), "epub2", str(tmp_path), meta))

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    monkeypatch.setattr(capture, "download_artwork_from_meta", AsyncMock(return_value=False))
    monkeypatch.setattr(capture, "extract_document_cover", AsyncMock(return_value=True))
    assert asyncio.run(capture.prepare_book_cover(str(pdf), "pdf", str(tmp_path), Meta()))
    monkeypatch.setattr(capture, "extract_document_cover", AsyncMock(return_value=False))
    assert asyncio.run(capture.prepare_book_cover(str(pdf), "pdf2", str(tmp_path), Meta())) is None


def test_download_artwork_redirects_mam_candidates_validation_and_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artwork = tmp_path / "cover.png"
    meta = Meta(artwork_url="https://www.myanonamouse.net/covers/book.webp", artwork_path="")
    capture.default_config = {"mam_api_key": "mam-key"}
    monkeypatch.setattr(capture, "is_valid_cover_image", lambda path: Path(path).exists())
    monkeypatch.setattr(capture, "is_valid_image_bytes", lambda data: data.startswith(b"image"))
    monkeypatch.setattr(capture, "is_public_http_url", lambda url: "private" not in url)

    class Response:
        def __init__(self, status: int, *, content: bytes = b"", location: str | None = None) -> None:
            self.status_code = status
            self.content = content
            self.headers = {"Location": location} if location is not None else {}
            self.is_redirect = 300 <= status < 400

    class Client:
        queue: ClassVar[list[Response]] = []
        calls: ClassVar[list[tuple[str, dict[str, str], dict[str, str]]]] = []

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, *, cookies: dict[str, str], headers: dict[str, str]) -> Response:
            type(self).calls.append((url, dict(cookies), dict(headers)))
            return type(self).queue.pop(0)

    monkeypatch.setattr("httpx.AsyncClient", Client)
    Client.queue = [Response(302, location="/covers/book.jpg"), Response(200, content=b"image-png")]
    assert asyncio.run(capture.download_artwork_from_meta(meta, str(artwork)))
    assert meta.artwork_path == str(artwork)
    assert Client.calls[0][1] == {"mam_id": "mam-key"}

    meta = Meta(artwork_url="https://host.invalid/private.png")
    Client.queue = [Response(200, content=b"image")]
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))

    meta = Meta(artwork_url="https://host.invalid/bad.png")
    Client.queue = [Response(200, content=b"not-an-image")]
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))

    meta = Meta(artwork_url="https://www.myanonamouse.net/covers/book.webp")
    Client.queue = [Response(404), Response(404), Response(404), Response(404)]
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))

    meta = Meta(artwork_url="https://host.invalid/redirect.png")
    Client.queue = [Response(302, location="/next.png") for _ in range(4)]
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))

    meta = Meta(artwork_url="https://host.invalid/missing-location.png")
    Client.queue = [Response(302)]
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))

    meta = Meta(artwork_url="https://host.invalid/error.png")
    Client.queue = [Response(500)]
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))


def test_epub_additional_standard_id_case_and_no_rootfile(tmp_path: Path) -> None:
    output = tmp_path / "cover.bin"
    no_root = tmp_path / "no-root.epub"
    _write_epub(no_root, {"META-INF/container.xml": "<container/>", "readme.txt": "none"})
    assert not asyncio.run(capture.extract_epub_cover(str(no_root), str(output)))

    standard = tmp_path / "standard.epub"
    _write_epub(
        standard,
        {
            "OPS/book.opf": '<package><manifest><item id="cover-image" href="Images/Cover.JPEG" media-type="application/octet-stream"/></manifest></package>',
            "OPS/images/cover.jpeg": b"case-cover",
        },
    )
    assert asyncio.run(capture.extract_epub_cover(str(standard), str(output)))
    assert output.read_bytes() == b"case-cover"

    confirmed = tmp_path / "confirmed-none.epub"
    _write_epub(
        confirmed,
        {
            "book.opf": '<package><manifest><item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/></manifest></package>',
            "cover.xhtml": "<html><body>No image</body></html>",
        },
    )
    assert not asyncio.run(capture.extract_epub_cover(str(confirmed), str(output), confirmed_only=True))


def test_generate_ebook_screenshots_cbz_cbr_pdf_epub_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fitz
    from PIL import Image

    meta = Meta(base_dir=str(tmp_path), uuid="comic", retake=False, artwork_path="")
    png = tmp_path / "page1.png"
    jpg = tmp_path / "page2.jpg"
    Image.new("RGB", (5, 5), "red").save(png)
    Image.new("RGB", (5, 5), "blue").save(jpg)
    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as archive:
        archive.write(jpg, "pages/page10.jpg")
        archive.write(png, "pages/page2.png")
    monkeypatch.setattr(capture, "prepare_book_cover", AsyncMock(return_value=None))
    monkeypatch.setattr("random.sample", lambda population, count: list(population)[:count])
    screenshots = asyncio.run(capture.generate_ebook_screenshots(str(cbz), "Comic", meta.uuid, meta.base_dir, meta, num_screens=2))
    assert len(screenshots) == 2
    assert Path(meta.artwork_path).is_file() and Path(meta.artwork_banner_path).is_file()

    banner = Path(meta.artwork_banner_path)
    meta.retake = False
    screenshots = asyncio.run(capture.generate_ebook_screenshots(str(cbz), "Comic", meta.uuid, meta.base_dir, meta, num_screens=1))
    assert len(screenshots) == 1 and meta.artwork_banner_path == str(banner)

    cbr = tmp_path / "comic.cbr"
    cbr.write_bytes(cbz.read_bytes())
    cbr_meta = Meta(base_dir=str(tmp_path), uuid="cbr", retake=False, artwork_path="")
    assert asyncio.run(capture.generate_ebook_screenshots(str(cbr), "Comic", cbr_meta.uuid, cbr_meta.base_dir, cbr_meta, num_screens=1))

    invalid = tmp_path / "invalid.cbz"
    invalid.write_bytes(b"bad archive")
    assert asyncio.run(capture.generate_ebook_screenshots(str(invalid), "Invalid", "invalid", str(tmp_path), Meta())) == []

    empty = tmp_path / "empty.cbz"
    _write_epub(empty, {"readme.txt": "none"})
    assert asyncio.run(capture.generate_ebook_screenshots(str(empty), "Empty", "empty", str(tmp_path), Meta())) == []

    pdf = tmp_path / "book.pdf"
    document = fitz.open()
    document.new_page()
    document.new_page()
    document.save(pdf)
    document.close()
    pdf_meta = Meta(base_dir=str(tmp_path), uuid="pdf", retake=False, artwork_path="")
    pages = asyncio.run(capture.generate_ebook_screenshots(str(pdf), "Book", pdf_meta.uuid, pdf_meta.base_dir, pdf_meta, num_screens=2))
    assert len(pages) == 2 and Path(pdf_meta.artwork_path).is_file()

    class EmptyDocument:
        def __len__(self) -> int:
            return 0

    original_open = fitz.open
    monkeypatch.setattr(fitz, "open", lambda *_args, **_kwargs: EmptyDocument())
    assert asyncio.run(capture.generate_ebook_screenshots(str(pdf), "Empty", "pdf-empty", str(tmp_path), Meta())) == []

    class Page:
        def get_pixmap(self, *, matrix: object) -> object:
            del matrix
            return SimpleNamespace(save=lambda path: Path(path).write_bytes(b"page"))

    class FakeDocument:
        def __len__(self) -> int:
            return 2

        def __getitem__(self, _index: int) -> Page:
            return Page()

        def close(self) -> None:
            return None

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"epub")
    monkeypatch.setattr(fitz, "open", lambda *_args, **_kwargs: FakeDocument())
    monkeypatch.setattr(capture, "extract_epub_cover", AsyncMock(side_effect=RuntimeError("cover failed")))
    epub_meta = Meta(base_dir=str(tmp_path), uuid="epub-generate", retake=False, artwork_path="")
    assert asyncio.run(capture.generate_ebook_screenshots(str(epub), "EPUB", epub_meta.uuid, epub_meta.base_dir, epub_meta, num_screens=1))

    monkeypatch.setattr(fitz, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("render failed")))
    assert asyncio.run(capture.generate_ebook_screenshots(str(pdf), "Broken", "broken-pdf", str(tmp_path), Meta())) == []
    monkeypatch.setattr(fitz, "open", original_open)


def test_unrar_configuration_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "configured_binary", lambda _name: "/usr/bin/unrar")
    invalid = tmp_path / "invalid.cbr"
    invalid.write_bytes(b"invalid")
    assert not asyncio.run(capture.extract_document_cover(str(invalid), str(tmp_path / "cover.png")))
    assert asyncio.run(capture.generate_ebook_screenshots(str(invalid), "Invalid", "unrar", str(tmp_path), Meta())) == []
    assert os.environ["UNRAR_TOOL"] == "/usr/bin/unrar"


def test_epub_meta_cover_html_without_image_parent_parts_and_empty_manifest(tmp_path: Path) -> None:
    output = tmp_path / "cover.bin"
    meta_image = tmp_path / "meta-image.epub"
    _write_epub(
        meta_image,
        {
            "META-INF/container.xml": '<container><rootfiles><rootfile full-path="OPS/sub/book.opf"/></rootfiles></container>',
            "OPS/sub/book.opf": '<package><metadata><meta name="cover" content="cover-id"/></metadata><manifest><item id="cover-id" href="./../images/cover.jpg" media-type="image/jpeg"/></manifest></package>',
            "OPS/images/cover.jpg": b"meta-cover",
        },
    )
    assert asyncio.run(capture.extract_epub_cover(str(meta_image), str(output)))
    assert output.read_bytes() == b"meta-cover"

    html_none = tmp_path / "html-none.epub"
    _write_epub(
        html_none,
        {
            "book.opf": '<package><metadata><meta name="cover" content="page"/></metadata><manifest><item id="page" href="cover.xhtml" media-type="application/xhtml+xml"/></manifest></package>',
            "cover.xhtml": "<html><body>No image element</body></html>",
        },
    )
    assert not asyncio.run(capture.extract_epub_cover(str(html_none), str(output)))

    empty_manifest = tmp_path / "empty-manifest.epub"
    _write_epub(empty_manifest, {"book.opf": "<package><manifest/></package>", "readme.txt": "none"})
    assert not asyncio.run(capture.extract_epub_cover(str(empty_manifest), str(output)))


def test_cbz_falls_back_to_rar_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import zipfile as zipfile_module

    import rarfile
    from PIL import Image

    source_image = tmp_path / "source.jpg"
    Image.new("RGB", (4, 4), "blue").save(source_image)
    archive = tmp_path / "fallback.cbz"
    archive.write_bytes(b"not zip")
    output = tmp_path / "cover.png"

    class FakeRar:
        def __init__(self, _path: str, _mode: str) -> None:
            pass

        def namelist(self) -> list[str]:
            return ["page1.jpg"]

        def extract(self, _name: str, target: Path) -> None:
            target.mkdir(parents=True, exist_ok=True)
            (target / "page1.jpg").write_bytes(source_image.read_bytes())

        def close(self) -> None:
            return None

    original_zip = zipfile_module.ZipFile
    monkeypatch.setattr(zipfile_module, "ZipFile", lambda *_args, **_kwargs: (_ for _ in ()).throw(zipfile_module.BadZipFile()))
    monkeypatch.setattr(rarfile, "RarFile", FakeRar)
    assert asyncio.run(capture.extract_document_cover(str(archive), str(output)))
    assert output.is_file()
    monkeypatch.setattr(zipfile_module, "ZipFile", original_zip)


def test_epub_relative_dot_component_is_normalized(tmp_path: Path) -> None:
    output = tmp_path / "cover.bin"
    epub = tmp_path / "dot-path.epub"
    _write_epub(
        epub,
        {
            "OPS/book.opf": '<package><manifest><item id="cover-image" href="./cover.jpg" media-type="image/jpeg" properties="cover-image"/></manifest></package>',
            "OPS/cover.jpg": b"dot-cover",
        },
    )
    assert asyncio.run(capture.extract_epub_cover(str(epub), str(output)))
    assert output.read_bytes() == b"dot-cover"


def test_epub_absolute_style_href_is_safely_resolved_inside_archive(tmp_path: Path) -> None:
    output = tmp_path / "cover.bin"
    epub = tmp_path / "absolute-href.epub"
    _write_epub(
        epub,
        {
            "OPS/book.opf": '<package><manifest><item id="cover-image" href="/cover.jpg" media-type="image/jpeg" properties="cover-image"/></manifest></package>',
            "cover.jpg": b"absolute-cover",
        },
    )
    assert asyncio.run(capture.extract_epub_cover(str(epub), str(output)))
    assert output.read_bytes() == b"absolute-cover"
