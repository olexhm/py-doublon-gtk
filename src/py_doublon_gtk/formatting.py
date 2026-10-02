"""Small pure helpers shared by the UI. No ``gi`` here either, so they stay testable."""

from pydantic import ValidationError

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(num_bytes: int) -> str:
    """Render a byte count the way a file manager would: 1 KB is 1000 B."""
    size = float(num_bytes)
    for unit in _UNITS[:-1]:
        if abs(size) < 1000:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} {_UNITS[-1]}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """`3 files`, `1 file` — the phrasing appears in toasts and dialogs everywhere."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def split_extensions(text: str) -> list[str]:
    """Split what the user typed into an extension list. Normalizing is the model's job."""
    return [part for part in text.replace(",", " ").split() if part]


def clean_validation_message(error: Exception) -> str:
    """Turn a pydantic error into one line fit for a toast.

    ``str(ValidationError)`` is multi-line, prefixes the real message with ``Value error, ``
    and appends ``[type=..., input_value=..., input_type=...]``. Reading ``errors()`` gets the
    message on its own; parsing the string representation drags all of that along.
    """
    if isinstance(error, ValidationError):
        details = error.errors()
        if details:
            return str(details[0]["msg"]).removeprefix("Value error, ")
    return str(error).splitlines()[-1].strip()
