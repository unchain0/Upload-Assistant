# ruff: noqa: S101
import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any, cast

import pytest as _pytest  # pyright: ignore[reportMissingImports]

import bin.get_zentag as get_zentag
import src.zentag as zentag
from src.meta import Meta

pytest: Any = cast(Any, _pytest)


@pytest.mark.asyncio
async def test_tampered_cached_zentag_is_replaced_with_verified_binary(tmp_path: Path, monkeypatch: Any) -> None:
    asset = "zentag_0.3.0_linux_amd64.tar.gz"
    payload = b"verified-zentag"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("zentag")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    archive_payload = archive_buffer.getvalue()

    target = tmp_path / "bin" / "zentag" / "linux" / "amd64"
    target.mkdir(parents=True)
    binary = target / "zentag"
    binary.write_bytes(b"tampered")
    (target / zentag.ZentagBinaryManager.VERSION).write_text(zentag.ZentagBinaryManager.VERSION, encoding="utf-8")

    class Response:
        content = archive_payload

        def __init__(self) -> None:
            self.headers = {"content-length": str(len(archive_payload))}

        async def __aenter__(self) -> Response:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        @staticmethod
        def raise_for_status() -> None:
            return None

        async def aiter_bytes(self, chunk_size: int):  # type: ignore[no-untyped-def]  # noqa: ARG002
            yield self.content

    class Client:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def stream(self, *_args: Any, **_kwargs: Any) -> Response:
            return Response()

    monkeypatch.setattr(get_zentag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_zentag.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(zentag.ZentagBinaryManager, "CHECKSUMS", {asset: hashlib.sha256(archive_payload).hexdigest()})
    monkeypatch.setattr(zentag.ZentagBinaryManager, "BINARY_CHECKSUMS", {asset: hashlib.sha256(payload).hexdigest()})
    monkeypatch.setattr(get_zentag, "HTTPX", type("HTTPXStub", (), {"AsyncClient": Client}))

    assert await zentag.ZentagBinaryManager.ensure_binary(tmp_path) == str(binary)
    assert binary.read_bytes() == payload


@pytest.mark.asyncio
async def test_noninteractive_zentag_process_does_not_inherit_terminal_stdin(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"ok", b""

    async def fake_create(*_command: str, **kwargs: Any) -> Process:
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(zentag.asyncio, "create_subprocess_exec", fake_create)

    assert await zentag._run_process(["zentag", "ebook", "book.pdf"]) == (0, "ok", "")
    assert captured["stdin"] == zentag.asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_unattended_zenith_m4b_is_transformed_and_validated(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book [B07ZHYPJK1].m4b"
    source.write_bytes(b"m4b")
    output = tmp_path / "zentag-output" / "Author - Book (2020) JPN {Narrator} [WEB] M4B AAC 64kbps"
    calls: list[list[str]] = []

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_transform(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        output.mkdir(parents=True)
        return 0, f"Proceed? [y/N]: Wrote {zentag.json.dumps(str(output))}\n", ""

    async def fake_process(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        return 0, "[]", ""

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_transform", fake_transform)
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(path=str(source), trackers=["ZENITH"], unattended=True)

    prepared = await zentag.prepare_zenith_audiobook(meta, str(tmp_path), {"DEFAULT": {"auto_zentag": True}})

    assert prepared == str(output.resolve())
    assert "--asin" in calls[0]
    assert calls[0][calls[0].index("--asin") + 1] == "B07ZHYPJK1"
    assert calls[1][-1] == "--json"


@pytest.mark.asyncio
async def test_unattended_zenith_ebook_is_organized_inside_directory(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Rich Dad Guide.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "zentag-output" / "Robert T. Kiyosaki - Rich Dad's Guide [ENG PDF]"
    output_file = output / "Robert T. Kiyosaki - Rich Dad's Guide (2012) [ENG PDF 1612680208].pdf"
    calls: list[list[str]] = []

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_process(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        if "check" in command:
            return 0, "[]", ""
        output.mkdir(parents=True)
        output_file.write_bytes(b"pdf")
        return 0, f"Wrote {output_file}\n", ""

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(
        path=str(source),
        filelist=[str(source)],
        trackers=["ZENITH"],
        unattended=True,
        category="BOOK",
        author="Robert T. Kiyosaki",
        title="Rich Dad's Guide",
        year=2012,
        isbn="1612680208",
        book_language_iso="eng",
    )

    assert await zentag.prepare_zenith_ebook(meta, str(tmp_path), {"DEFAULT": {"auto_zentag": True}}) == str(output)
    assert calls[0][3:5] == ["ebook", str(source)]
    assert calls[0][calls[0].index("--isbn") + 1] == "1612680208"
    assert calls[0][calls[0].index("--language") + 1] == "eng"
    assert calls[1][-2:] == [str(output), "--json"]


@pytest.mark.asyncio
async def test_zenith_ebook_with_compliance_violations_is_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "zentag-output" / "Author - Book [ENG PDF]"
    output_file = output / "Author - Book (2026) [ENG PDF 9780000000002].pdf"

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_process(command: list[str]) -> tuple[int, str, str]:
        if "check" in command:
            return 0, '[{"rule":"ebook-naming"}]', ""
        output.mkdir(parents=True)
        output_file.write_bytes(b"pdf")
        return 0, f"Wrote {output_file}\n", ""

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(
        path=str(source),
        filelist=[str(source)],
        trackers=["ZENITH"],
        unattended=True,
        category="BOOK",
        author="Author",
        title="Book",
        year=2026,
        isbn="9780000000002",
        book_language_iso="eng",
    )

    assert await zentag.prepare_zenith_ebook(meta, str(tmp_path), {"DEFAULT": {"auto_zentag": True}}) is None


@pytest.mark.asyncio
async def test_failed_zentag_validation_keeps_original_for_other_trackers(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book [B07ZHYPJK1].m4b"
    source.write_bytes(b"m4b")
    output = tmp_path / "zentag-output" / "Book"

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_transform(_command: list[str]) -> tuple[int, str, str]:
        output.mkdir(parents=True)
        return 0, f"Wrote {zentag.json.dumps(str(output))}\n", ""

    async def fake_process(_command: list[str]) -> tuple[int, str, str]:
        return 1, '[{"rule":"naming"}]', "violations found"

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_transform", fake_transform)
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(path=str(source), trackers=["ZENITH", "YUSCENE"], unattended=True)

    assert await zentag.prepare_zenith_audiobook(meta, str(tmp_path), {"DEFAULT": {"auto_zentag": True}}) is None


@pytest.mark.asyncio
async def test_debug_runs_zentag_without_uploading(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book.m4b"
    source.write_bytes(b"m4b")
    output = tmp_path / "zentag-output" / "Author - Book"

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_transform(_command: list[str]) -> tuple[int, str, str]:
        output.mkdir(parents=True)
        return 0, f"Wrote {zentag.json.dumps(str(output))}\n", ""

    async def fake_process(_command: list[str]) -> tuple[int, str, str]:
        return 0, "[]", ""

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_transform", fake_transform)
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(path=str(source), trackers=["ZENITH"], unattended=True, debug=True)

    assert await zentag.prepare_zenith_audiobook(meta, str(tmp_path), {"DEFAULT": {}}) == str(output.resolve())
