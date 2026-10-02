"""Pins the behaviour of the original `main.py` script before it was deleted.

The old script walked the tree with os.walk, hashed every file with SHA-256, kept the first
copy it happened to meet and deleted the rest. Walk order is arbitrary, so *which* copy
survived was not defined — but the shape of the outcome was: exactly one file per distinct
content, and every other byte-identical copy gone. The new scanner must still produce that,
with its defaults and without deleting anything itself.
"""

import hashlib
from pathlib import Path

from py_doublon_gtk.models import ScanOptions
from py_doublon_gtk.scanner import scan

TREE = {
    "photo.jpg": "AAAA",
    "copy of photo.jpg": "AAAA",
    "sub/photo.jpg": "AAAA",
    "sub/notes.txt": "BBBB",
    "sub/deep/notes.txt": "BBBB",
    "unique.bin": "CCCC",
    "empty_one": "",
    "sub/empty_two": "",
}


def legacy_duplicates(folder: str) -> list[str]:
    """The original algorithm, verbatim apart from not deleting anything."""
    import os

    hashes: dict[str, str] = {}
    duplicates: list[str] = []
    for root, _, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)  # noqa: PTH118
            hasher = hashlib.sha256()
            with open(path, "rb") as handle:  # noqa: PTH123
                while chunk := handle.read(8192):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            if digest in hashes:
                duplicates.append(path)
            else:
                hashes[digest] = path
    return duplicates


def build(root: Path) -> None:
    for name, content in TREE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def survivors(root: Path) -> set[str]:
    return {
        hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()
    }


def test_new_defaults_reproduce_the_old_outcome(tmp_path):
    legacy_root = tmp_path / "legacy"
    new_root = tmp_path / "new"
    legacy_root.mkdir()
    new_root.mkdir()
    build(legacy_root)
    build(new_root)

    legacy = legacy_duplicates(str(legacy_root))
    result = scan(ScanOptions(folder=new_root))

    # Same number of files condemned...
    assert result.duplicate_count == len(legacy)
    # ...and applying them leaves exactly one copy of each distinct content, as before.
    for path in result.duplicates:
        path.unlink()
    for path in legacy:
        Path(path).unlink()
    assert survivors(new_root) == survivors(legacy_root)
    assert len(survivors(new_root)) == len(set(TREE.values()))


def test_a_scan_alone_deletes_nothing(tmp_path):
    build(tmp_path)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    scan(ScanOptions(folder=tmp_path))

    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before
