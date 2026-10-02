"""Removing files — the one non-UI place that touches GIO.

``gi`` is imported lazily inside :class:`GioTrashRemover.__init__`, so importing this module
in a test pulls in no GTK, no display and no session bus.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from py_doublon_gtk.models import RemovalFailure, RemovalReport


class TrashUnsupportedError(RuntimeError):
    """The mount holding this file has no trash (tmpfs, most network and removable media)."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Trash is not available on the volume holding {path}")
        self.path = path


class Remover(Protocol):
    """How a single file is disposed of.

    ``trashed`` is the discriminating attribute and lives here on purpose: shared code asks
    the protocol whether removal is recoverable, and never does ``isinstance(...)`` against a
    concrete class — that would couple the generic loop to ``gi``.
    """

    trashed: bool

    def remove(self, path: Path) -> None: ...


class PermanentRemover:
    """Unlink. Pure stdlib, tested directly, and irreversible."""

    trashed = False

    def remove(self, path: Path) -> None:
        path.unlink()


class GioTrashRemover:
    """Move to the desktop trash, so the user can undo a mistake."""

    trashed = True

    def __init__(self) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        self._gio = Gio
        self._error = GLib.Error

    def remove(self, path: Path) -> None:
        try:
            self._gio.File.new_for_path(str(path)).trash(None)
        except self._error as error:
            if error.matches(self._gio.io_error_quark(), self._gio.IOErrorEnum.NOT_SUPPORTED):
                raise TrashUnsupportedError(path) from error
            raise


def remove_all(paths: Iterable[Path], remover: Remover) -> RemovalReport:
    """Remove every path, collecting per-file failures instead of stopping at the first.

    The exception is a missing trash: every file in one batch is normally on the same mount,
    so retrying the remaining N is guaranteed to fail identically. That one fails fast.
    """
    removed: list[Path] = []
    failures: list[RemovalFailure] = []
    for path in paths:
        try:
            remover.remove(path)
        except TrashUnsupportedError:
            raise
        except (OSError, RuntimeError) as error:
            # GLib.Error subclasses RuntimeError, not OSError: catching only OSError here
            # would let every GIO failure escape.
            failures.append(RemovalFailure(path=path, message=str(error)))
        else:
            removed.append(path)
    return RemovalReport(removed=tuple(removed), failures=tuple(failures), trashed=remover.trashed)
