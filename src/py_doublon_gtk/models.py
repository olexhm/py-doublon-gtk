"""Validated user input and scan results.

Pure data: this module must never import ``gi``. Everything the user types is parsed once,
here, into frozen models; nothing below the UI re-parses or re-validates.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class KeepStrategy(StrEnum):
    """Which member of a group of identical files survives."""

    OLDEST = "oldest"
    NEWEST = "newest"
    SHORTEST_PATH = "shortest-path"


class ScanOptions(BaseModel):
    """A validated scan request. Frozen, so it cannot drift out of validity downstream."""

    model_config = ConfigDict(frozen=True)

    folder: Path
    recursive: bool = True
    extensions: list[str] = Field(default_factory=list[str])
    min_size: int = Field(default=0, ge=0)
    keep: KeepStrategy = KeepStrategy.OLDEST
    use_trash: bool = True

    @field_validator("folder")
    @classmethod
    def _expand_folder(cls, value: Path) -> Path:
        return value.expanduser().absolute()

    @field_validator("extensions")
    @classmethod
    def _normalize_extensions(cls, values: list[str]) -> list[str]:
        # Normalize *before* de-duplicating: [".JPG", "jpg"] is one extension, not two, and
        # de-duplicating first would leave both entries in the list.
        normalized: list[str] = []
        for raw in values:
            candidate = raw.strip().lower().lstrip(".")
            if not candidate:
                continue
            if "/" in candidate or candidate != candidate.strip():
                raise ValueError(f"{raw!r} is not a file extension")
            extension = f".{candidate}"
            if extension not in normalized:
                normalized.append(extension)
        return normalized

    @model_validator(mode="after")
    def _check_folder_is_a_directory(self) -> Self:
        if not self.folder.exists():
            raise ValueError(f"Folder does not exist: {self.folder}")
        if not self.folder.is_dir():
            raise ValueError(f"Not a folder: {self.folder}")
        return self

    def matches(self, path: Path, size: int) -> bool:
        """Whether a file is a candidate for hashing."""
        if size < self.min_size:
            return False
        return not self.extensions or path.suffix.lower() in self.extensions


class DuplicateGroup(BaseModel):
    """Files with identical content. ``keeper`` survives; ``duplicates`` are removable."""

    model_config = ConfigDict(frozen=True)

    digest: str
    size: int
    keeper: Path
    duplicates: tuple[Path, ...]

    @property
    def reclaimable_bytes(self) -> int:
        return self.size * len(self.duplicates)


class ScanResult(BaseModel):
    """The outcome of one scan, including per-file problems that did not stop it."""

    model_config = ConfigDict(frozen=True)

    groups: tuple[DuplicateGroup, ...]
    files_scanned: int
    errors: tuple[str, ...]

    @property
    def duplicates(self) -> tuple[Path, ...]:
        return tuple(path for group in self.groups for path in group.duplicates)

    @property
    def duplicate_count(self) -> int:
        return sum(len(group.duplicates) for group in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(group.reclaimable_bytes for group in self.groups)


class RemovalFailure(BaseModel):
    """One file that could not be removed. The rest of the batch continued."""

    model_config = ConfigDict(frozen=True)

    path: Path
    message: str


class RemovalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    removed: tuple[Path, ...]
    failures: tuple[RemovalFailure, ...]
    trashed: bool
