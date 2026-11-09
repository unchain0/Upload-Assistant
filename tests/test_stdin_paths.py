import io
import shlex
from pathlib import Path

import pytest

from src.delivery.cli.arguments import (
    partition_existing_paths,
    read_paths_from_stdin,
)
from src.domain_models.release import Meta
from src.services.queue_service import QueueManager


class InteractiveInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_read_paths_from_interactive_stdin_preserves_shell_sensitive_characters() -> (
    None
):
    stream = InteractiveInput(
        "/media/First Release (2026).mkv\n/media/[Group] Second.mkv\n\nignored.mkv\n"
    )

    remaining_args, paths = read_paths_from_stdin(
        ["-ua", "-sda", "--paths-from-stdin"], stream
    )

    assert remaining_args == ["-ua", "-sda"]
    assert paths == [
        "/media/First Release (2026).mkv",
        "/media/[Group] Second.mkv",
    ]


def test_read_paths_from_piped_stdin_ignores_blank_lines() -> None:
    stream = io.StringIO("/media/First.mkv\n\n/media/Second.mkv\n")

    remaining_args, paths = read_paths_from_stdin(
        ["--paths-from-stdin", "-debug"], stream
    )

    assert remaining_args == ["-debug"]
    assert paths == ["/media/First.mkv", "/media/Second.mkv"]


def test_read_paths_from_stdin_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="did not receive any paths"):
        read_paths_from_stdin(["--paths-from-stdin"], io.StringIO("\n"))


def test_partition_existing_paths_keeps_valid_entries_from_mixed_batch(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "Valid Book.epub"
    valid.touch()
    missing = tmp_path / "Missing Book.epub"

    existing, absent = partition_existing_paths([str(missing), str(valid)])

    assert existing == [str(valid.resolve())]
    assert absent == [str(missing)]


def test_partition_existing_paths_reports_fully_invalid_batch(
    tmp_path: Path,
) -> None:
    first = tmp_path / "First.epub"
    second = tmp_path / "Second.epub"

    existing, absent = partition_existing_paths([str(first), str(second)])

    assert existing == []
    assert absent == [str(first), str(second)]


@pytest.mark.asyncio
async def test_queue_uses_multiple_explicit_existing_paths(
    tmp_path: Path,
) -> None:
    first = tmp_path / "First Release (2026).mkv"
    second = tmp_path / "Second [Group].mkv"
    first.touch()
    second.touch()
    paths = [str(first.resolve()), str(second.resolve())]

    queue, _ = await QueueManager.handle_queue(
        " ".join(paths), Meta(), paths, str(tmp_path)
    )

    assert queue == paths


@pytest.mark.asyncio
async def test_stdin_txt_path_is_content_not_queue_manifest(
    tmp_path: Path,
) -> None:
    document = tmp_path / "Pocket PC Serials.txt"
    document.write_text("not/a/queued/path", encoding="utf-8")
    meta = Meta(paths_from_stdin=True)

    queue, _ = await QueueManager.handle_queue(
        str(document), meta, [str(document)], str(tmp_path)
    )

    assert queue == [str(document)]
    assert not meta.args_line_queue


def test_read_paths_from_stdin_deduplicates_repeated_paths() -> None:
    stream = io.StringIO(
        "/media/First.mkv\n/media/First.mkv\n/media/Second.mkv\n/media/Second.mkv\n"
    )

    remaining_args, paths = read_paths_from_stdin(
        ["--paths-from-stdin", "-debug"], stream
    )

    assert remaining_args == ["-debug"]
    assert paths == ["/media/First.mkv", "/media/Second.mkv"]


@pytest.mark.asyncio
async def test_queue_manager_deduplicates_paths(tmp_path: Path) -> None:
    first = tmp_path / "First.mkv"
    second = tmp_path / "Second.mkv"
    first.touch()
    second.touch()
    queue, _ = await QueueManager.handle_queue(
        str(first),
        Meta(),
        [str(first), str(second), str(first)],
        str(tmp_path),
    )

    assert queue == [str(first), str(second)]


@pytest.mark.asyncio
async def test_queue_splits_same_ebook_in_distinct_formats(
    tmp_path: Path,
) -> None:
    release = tmp_path / "How to Live_ An Ancient Guide to a Happy Life (eng)"
    release.mkdir()
    stem = "How to Live_ An Ancient Guide to a Happy Life - Seneca & James S. Romm"
    epub = release / f"{stem}.epub"
    pdf = release / f"{stem}.pdf"
    epub.write_bytes(b"epub")
    pdf.write_bytes(b"pdf")

    queue, _ = await QueueManager.handle_queue(
        str(release), Meta(), [str(release)], str(tmp_path)
    )

    assert queue == [str(epub.resolve()), str(pdf.resolve())]


@pytest.mark.asyncio
async def test_queue_splits_distinct_ebooks(tmp_path: Path) -> None:
    release = tmp_path / "Books"
    release.mkdir()
    (release / "First Book.epub").write_bytes(b"epub")
    (release / "Second Book.pdf").write_bytes(b"pdf")

    queue, _ = await QueueManager.handle_queue(
        str(release), Meta(), [str(release)], str(tmp_path)
    )

    assert queue == [
        str((release / "First Book.epub").resolve()),
        str((release / "Second Book.pdf").resolve()),
    ]


@pytest.mark.asyncio
async def test_args_line_queue_splits_ebooks_and_preserves_options(
    tmp_path: Path,
) -> None:
    release = tmp_path / "Books"
    release.mkdir()
    first = release / "First Book.epub"
    second = release / "Second Book.pdf"
    first.write_bytes(b"epub")
    second.write_bytes(b"pdf")
    queue_file = tmp_path / "books.txt"
    queue_file.write_text(
        f'"{release}" -tk SITE -btitle "Shared title"\n', encoding="utf-8"
    )

    queue, _ = await QueueManager.handle_queue(
        str(queue_file), Meta(), [str(queue_file)], str(tmp_path)
    )

    assert [item["path"] for item in queue] == [
        str(first.resolve()),
        str(second.resolve()),
    ]
    for item in queue:
        assert item["args"][1:] == ["-tk", "SITE", "-btitle", "Shared title"]
        assert item["line"] == shlex.join(item["args"])
