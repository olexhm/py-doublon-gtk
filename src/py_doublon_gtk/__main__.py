"""Entry point: ``py-doublon-gtk`` and ``python -m py_doublon_gtk``."""

from py_doublon_gtk.ui.application import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
