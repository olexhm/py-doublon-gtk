import os
from pathlib import Path

import pytest

from py_doublon_gtk import scanner
from py_doublon_gtk.models import KeepStrategy, ScanOptions
from py_doublon_gtk.scanner import scan


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def build(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_finds_duplicates_across_subfolders(tmp_path):
    build(tmp_path, {"a.txt": "same", "sub/b.txt": "same", "c.txt": "other"})

    result = scan(ScanOptions(folder=tmp_path))

    assert result.files_scanned == 3
    assert len(result.groups) == 1
    assert result.duplicate_count == 1
    assert result.errors == ()


def test_scanning_changes_nothing_on_disk(tmp_path):
    build(tmp_path, {"a.txt": "same", "sub/b.txt": "same"})
    before = snapshot(tmp_path)

    scan(ScanOptions(folder=tmp_path))

    assert snapshot(tmp_path) == before


def test_non_recursive_ignores_subfolders(tmp_path):
    build(tmp_path, {"a.txt": "same", "sub/b.txt": "same"})

    result = scan(ScanOptions(folder=tmp_path, recursive=False))

    assert result.files_scanned == 1
    assert result.groups == ()


def test_extension_filter_is_case_insensitive(tmp_path):
    build(tmp_path, {"a.JPG": "same", "b.jpg": "same", "c.txt": "same"})

    result = scan(ScanOptions(folder=tmp_path, extensions=["jpg"]))

    assert result.files_scanned == 2
    assert result.duplicate_count == 1


def test_minimum_size_skips_small_files(tmp_path):
    build(tmp_path, {"a.txt": "hi", "b.txt": "hi"})

    assert scan(ScanOptions(folder=tmp_path, min_size=10)).groups == ()
    assert scan(ScanOptions(folder=tmp_path, min_size=2)).duplicate_count == 1


def test_same_size_different_content_is_not_a_duplicate(tmp_path):
    build(tmp_path, {"a.txt": "aaaa", "b.txt": "bbbb"})

    assert scan(ScanOptions(folder=tmp_path)).groups == ()


def test_unique_sizes_are_never_hashed(tmp_path, monkeypatch):
    build(tmp_path, {"a.txt": "a", "b.txt": "bb", "c.txt": "ccc"})
    hashed: list[Path] = []

    def spy(path: Path, **_: object) -> str:
        hashed.append(path)
        return "x"

    monkeypatch.setattr(scanner, "file_digest", spy)

    assert scan(ScanOptions(folder=tmp_path)).groups == ()
    assert hashed == []


def test_symlinks_are_neither_followed_nor_reported(tmp_path):
    build(tmp_path, {"real/a.txt": "same", "real/b.txt": "same"})
    (tmp_path / "link_to_file").symlink_to(tmp_path / "real/a.txt")
    (tmp_path / "loop").symlink_to(tmp_path)

    result = scan(ScanOptions(folder=tmp_path))

    assert result.files_scanned == 2
    assert result.duplicate_count == 1
    assert all("link_to_file" not in str(path) for path in result.duplicates)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unreadable_directory_lands_in_errors(tmp_path):
    build(tmp_path, {"a.txt": "same", "b.txt": "same", "locked/c.txt": "same"})
    locked = tmp_path / "locked"
    locked.chmod(0o000)
    try:
        result = scan(ScanOptions(folder=tmp_path))
    finally:
        locked.chmod(0o755)

    assert result.duplicate_count == 1
    assert len(result.errors) == 1
    assert "locked" in result.errors[0]


def _touch(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def test_keep_oldest_is_the_default(tmp_path):
    build(tmp_path, {"new.txt": "same", "old.txt": "same"})
    _touch(tmp_path / "old.txt", 1_000)
    _touch(tmp_path / "new.txt", 2_000)

    result = scan(ScanOptions(folder=tmp_path))

    assert result.groups[0].keeper == tmp_path / "old.txt"
    assert result.duplicates == (tmp_path / "new.txt",)


def test_keep_newest(tmp_path):
    build(tmp_path, {"new.txt": "same", "old.txt": "same"})
    _touch(tmp_path / "old.txt", 1_000)
    _touch(tmp_path / "new.txt", 2_000)

    result = scan(ScanOptions(folder=tmp_path, keep=KeepStrategy.NEWEST))

    assert result.groups[0].keeper == tmp_path / "new.txt"


def test_keep_shortest_path(tmp_path):
    build(tmp_path, {"a.txt": "same", "deep/nested/b.txt": "same"})

    result = scan(ScanOptions(folder=tmp_path, keep=KeepStrategy.SHORTEST_PATH))

    assert result.groups[0].keeper == tmp_path / "a.txt"


def test_group_reports_reclaimable_bytes(tmp_path):
    build(tmp_path, {"a.txt": "0123456789", "b.txt": "0123456789", "c.txt": "0123456789"})

    result = scan(ScanOptions(folder=tmp_path))

    assert result.groups[0].size == 10
    assert result.reclaimable_bytes == 20


def test_file_digest_matches_hashlib(tmp_path):
    import hashlib

    target = tmp_path / "a.bin"
    payload = b"x" * 5000
    target.write_bytes(payload)

    assert scanner.file_digest(target, chunk_size=64) == hashlib.sha256(payload).hexdigest()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_file_lands_in_errors(tmp_path):
    build(tmp_path, {"a.txt": "same", "b.txt": "same", "locked.txt": "same"})
    locked = tmp_path / "locked.txt"
    locked.chmod(0o000)
    try:
        result = scan(ScanOptions(folder=tmp_path))
    finally:
        locked.chmod(0o644)

    assert result.duplicate_count == 1
    assert len(result.errors) == 1
    assert "locked.txt" in result.errors[0]


def test_file_vanishing_between_walk_and_stat_is_reported(tmp_path, monkeypatch):
    build(tmp_path, {"a.txt": "same", "b.txt": "same"})
    real_stat = Path.stat

    def flaky(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "b.txt":
            raise FileNotFoundError(2, "No such file or directory")
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", flaky)
    result = scan(ScanOptions(folder=tmp_path))

    assert result.groups == ()
    assert "b.txt" in result.errors[0]


def test_entry_vanishing_during_the_walk_is_reported(tmp_path, monkeypatch):
    build(tmp_path, {"a.txt": "content"})
    real_is_dir = Path.is_dir

    def flaky(self: Path, *, follow_symlinks: bool = True) -> bool:
        if self.name == "a.txt":
            raise PermissionError(13, "Permission denied")
        return real_is_dir(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "is_dir", flaky)
    result = scan(ScanOptions(folder=tmp_path))

    assert result.files_scanned == 0
    assert "a.txt" in result.errors[0]
