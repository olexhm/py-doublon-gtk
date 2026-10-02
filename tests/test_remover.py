from pathlib import Path

import pytest

from py_doublon_gtk.models import RemovalReport
from py_doublon_gtk.remover import (
    PermanentRemover,
    Remover,
    TrashUnsupportedError,
    remove_all,
)


class FakeRemover:
    """Stands in for the GIO path: no gi, no session bus, and it counts its calls."""

    trashed = True

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.attempts: list[Path] = []
        self._fail_with = fail_with

    def remove(self, path: Path) -> None:
        self.attempts.append(path)
        if self._fail_with is not None:
            raise self._fail_with


def test_fakes_and_the_real_thing_satisfy_the_protocol():
    removers: list[Remover] = [PermanentRemover(), FakeRemover()]
    assert [remover.trashed for remover in removers] == [False, True]


def test_permanent_remover_unlinks(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    PermanentRemover().remove(target)

    assert not target.exists()


def test_remove_all_reports_what_it_removed(tmp_path):
    paths = []
    for name in ("a.txt", "b.txt"):
        path = tmp_path / name
        path.write_text("x")
        paths.append(path)

    report = remove_all(paths, PermanentRemover())

    assert isinstance(report, RemovalReport)
    assert report.removed == tuple(paths)
    assert report.failures == ()
    assert report.trashed is False
    assert not any(path.exists() for path in paths)


def test_per_item_failures_do_not_stop_the_batch(tmp_path):
    missing = tmp_path / "gone.txt"
    present = tmp_path / "here.txt"
    present.write_text("x")

    report = remove_all([missing, present], PermanentRemover())

    assert report.removed == (present,)
    assert [failure.path for failure in report.failures] == [missing]
    assert not present.exists()


def test_glib_style_runtime_errors_are_collected_not_raised(tmp_path):
    # GLib.Error is a RuntimeError, not an OSError; catching only OSError would miss it.
    remover = FakeRemover(fail_with=RuntimeError("g-io-error-quark: permission denied"))

    report = remove_all([tmp_path / "a", tmp_path / "b"], remover)

    assert report.removed == ()
    assert len(report.failures) == 2
    assert "permission denied" in report.failures[0].message


def test_missing_trash_fails_fast(tmp_path):
    paths = [tmp_path / f"{index}.txt" for index in range(5)]
    remover = FakeRemover(fail_with=TrashUnsupportedError(paths[0]))

    with pytest.raises(TrashUnsupportedError):
        remove_all(paths, remover)

    # Every file in a batch is normally on the same mount: do not retry the other four.
    assert len(remover.attempts) == 1


def test_gio_trash_remover_is_available():
    pytest.importorskip("gi")
    from py_doublon_gtk.remover import GioTrashRemover

    assert GioTrashRemover().trashed is True


def test_gio_trash_remover_either_trashes_or_reports_no_trash(tmp_path):
    """The GIO path must never surface a raw GLib.Error.

    tmp_path is often on tmpfs, which has no trash, so both outcomes are legitimate — what
    matters is that the NOT_SUPPORTED GError arrives as TrashUnsupportedError.
    """
    pytest.importorskip("gi")
    from py_doublon_gtk.remover import GioTrashRemover

    target = tmp_path / "a.txt"
    target.write_text("x")

    try:
        GioTrashRemover().remove(target)
    except TrashUnsupportedError as error:
        assert error.path == target
        assert target.exists()
    else:
        assert not target.exists()
