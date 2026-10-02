"""The Adw.Application."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from py_doublon_gtk.ui.window import PyDoublonGtkWindow  # noqa: E402

APP_ID = "io.github.olexhm.PyDoublonGtk"


class PyDoublonGtkApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

        about_action = Gio.SimpleAction(name="about")
        about_action.connect("activate", lambda *_: self._show_about())
        self.add_action(about_action)

    def _show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="py-doublon-gtk",
            application_icon="edit-delete-symbolic",
            developer_name="olexhm",
            version="0.1.0",
            comments="Find byte-identical files and move the extra copies to the trash.",
        )
        about.present(self.props.active_window)

    def do_activate(self) -> None:
        # Reuse the existing window so a second launch raises it instead of duplicating it.
        window = self.props.active_window or PyDoublonGtkWindow(application=self)
        window.present()


def main() -> int:
    application = PyDoublonGtkApplication()
    return application.run(sys.argv)
