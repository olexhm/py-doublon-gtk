"""Duplicate detection: pure functions over a directory tree.

No ``gi``, no side effects — the scan reads the filesystem and returns data. Removing what
it finds is somebody else's job (see :mod:`py_doublon_gtk.remover`).
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from py_doublon_gtk.models import DuplicateGroup, KeepStrategy, ScanOptions, ScanResult

CHUNK_SIZE = 1 << 20


@dataclass(frozen=True, slots=True)
class FileInfo:
    """A candidate file and the stat data the keep strategies need."""

    path: Path
    size: int
    mtime: float


def file_digest(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the SHA-256 of a file, read in chunks so large files stay out of memory."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def _walk(root: Path, recursive: bool, errors: list[str]) -> Iterator[Path]:
    """Yield regular files under ``root``. Symlinks are never followed nor yielded."""
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError as error:
            errors.append(f"{directory}: {error.strerror or error}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        stack.append(entry)
                elif entry.is_file():
                    yield entry
            except OSError as error:
                errors.append(f"{entry}: {error.strerror or error}")


def collect_files(options: ScanOptions) -> tuple[list[FileInfo], list[str]]:
    """Return the files matching ``options``, plus the directories/files that could not be read."""
    errors: list[str] = []
    files: list[FileInfo] = []
    for path in _walk(options.folder, options.recursive, errors):
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError as error:
            errors.append(f"{path}: {error.strerror or error}")
            continue
        if options.matches(path, stat.st_size):
            files.append(FileInfo(path=path, size=stat.st_size, mtime=stat.st_mtime))
    return files, errors


def _sort_key(strategy: KeepStrategy, info: FileInfo) -> tuple[float | int, str]:
    """Rank files so that the *first* one is the keeper."""
    match strategy:
        case KeepStrategy.OLDEST:
            return (info.mtime, str(info.path))
        case KeepStrategy.NEWEST:
            return (-info.mtime, str(info.path))
        case KeepStrategy.SHORTEST_PATH:
            return (len(str(info.path)), str(info.path))


def scan(options: ScanOptions) -> ScanResult:
    """Find groups of byte-identical files under ``options.folder``."""
    files, errors = collect_files(options)

    by_size: defaultdict[int, list[FileInfo]] = defaultdict(list)
    for info in files:
        by_size[info.size].append(info)

    by_digest: defaultdict[str, list[FileInfo]] = defaultdict(list)
    for candidates in by_size.values():
        if len(candidates) < 2:
            continue  # a unique size cannot be a duplicate; no point hashing it
        for info in candidates:
            try:
                digest = file_digest(info.path)
            except OSError as error:
                errors.append(f"{info.path}: {error.strerror or error}")
                continue
            by_digest[digest].append(info)

    groups: list[DuplicateGroup] = []
    for digest, members in by_digest.items():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda info: _sort_key(options.keep, info))
        keeper, *rest = ranked
        groups.append(
            DuplicateGroup(
                digest=digest,
                size=keeper.size,
                keeper=keeper.path,
                duplicates=tuple(info.path for info in rest),
            )
        )

    groups.sort(key=lambda group: str(group.keeper))
    return ScanResult(groups=tuple(groups), files_scanned=len(files), errors=tuple(errors))
