from pathlib import Path

import pytest
from pydantic import ValidationError

from py_doublon_gtk.models import KeepStrategy, ScanOptions


def test_extensions_are_normalized_then_deduplicated(tmp_path):
    options = ScanOptions(folder=tmp_path, extensions=["jpg", ".JPG", "  PNG ", ""])
    # Normalizing first is what collapses "jpg" and ".JPG"; de-duplicating first would not.
    assert options.extensions == [".jpg", ".png"]


def test_extension_with_a_separator_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="not a file extension"):
        ScanOptions(folder=tmp_path, extensions=["some/path"])


def test_folder_is_expanded_and_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    assert ScanOptions(folder=Path("sub")).folder == tmp_path / "sub"


def test_missing_folder_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="Folder does not exist"):
        ScanOptions(folder=tmp_path / "nope")


def test_file_instead_of_folder_is_rejected(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    with pytest.raises(ValidationError, match="Not a folder"):
        ScanOptions(folder=target)


def test_negative_minimum_size_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        ScanOptions(folder=tmp_path, min_size=-1)


def test_options_are_frozen(tmp_path):
    options = ScanOptions(folder=tmp_path)
    with pytest.raises(ValidationError):
        options.recursive = False


def test_defaults_match_the_documented_behaviour(tmp_path):
    options = ScanOptions(folder=tmp_path)
    assert options.recursive is True
    assert options.use_trash is True
    assert options.keep is KeepStrategy.OLDEST
    assert options.min_size == 0
    assert options.extensions == []


def test_matches_applies_extension_and_size(tmp_path):
    options = ScanOptions(folder=tmp_path, extensions=["jpg"], min_size=10)
    assert options.matches(tmp_path / "a.JPG", 20)
    assert not options.matches(tmp_path / "a.png", 20)
    assert not options.matches(tmp_path / "a.jpg", 9)
