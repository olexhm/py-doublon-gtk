#!/bin/sh
# Recreate the virtualenv from scratch.
#
# The --system-site-packages flag is NOT optional: PyGObject is sdist-only on PyPI and
# cannot be built here, so `gi` comes from the distro package (python3-gobject) and is only
# visible inside the venv with this flag. A bare `uv venv` produces an environment where
# `import gi` fails with an unexplained ImportError.
#
# Always run this script instead of `uv venv`.
set -eu

cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-/usr/bin/python3.14} # must match the interpreter python3-gobject was built for

rm -rf .venv
uv venv --system-site-packages -p "$PYTHON"
uv sync

uv run python -c "
import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw, Gtk
print(f'gi {gi.__version__}, GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}, Adw 1.{Adw.MINOR_VERSION}')"
