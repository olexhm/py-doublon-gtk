import pytest
from pydantic import ValidationError

from py_doublon_gtk.formatting import (
    clean_validation_message,
    format_size,
    plural,
    split_extensions,
)
from py_doublon_gtk.models import ScanOptions


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 KB"),
        (1_500_000, "1.5 MB"),
        (2_000_000_000, "2.0 GB"),
    ],
)
def test_format_size(num_bytes, expected):
    assert format_size(num_bytes) == expected


def test_plural():
    assert plural(1, "file") == "1 file"
    assert plural(0, "file") == "0 files"
    assert plural(2, "more group") == "2 more groups"


def test_split_extensions_accepts_commas_and_spaces():
    assert split_extensions("jpg, .PNG  gif,") == ["jpg", ".PNG", "gif"]
    assert split_extensions("   ") == []


def test_clean_validation_message_is_one_user_facing_line(tmp_path):
    with pytest.raises(ValidationError) as caught:
        ScanOptions(folder=tmp_path / "nope")

    message = clean_validation_message(caught.value)

    # Fit for a toast: no prefix, no newline, and none of pydantic's [type=...] trailer.
    assert message == f"Folder does not exist: {tmp_path / 'nope'}"


def test_format_size_tops_out_at_petabytes():
    assert format_size(3 * 1000**5) == "3.0 PB"


def test_clean_validation_message_handles_a_plain_constraint_error(tmp_path):
    with pytest.raises(ValidationError) as caught:
        ScanOptions(folder=tmp_path, min_size=-1)

    message = clean_validation_message(caught.value)

    assert message == "Input should be greater than or equal to 0"


def test_clean_validation_message_falls_back_for_other_exceptions():
    assert clean_validation_message(OSError("first line\nlast line")) == "last line"
