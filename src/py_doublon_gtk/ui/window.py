"""The main window: everything the user sees."""

import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from py_doublon_gtk.formatting import (  # noqa: E402
    clean_validation_message,
    format_size,
    plural,
    split_extensions,
)
from py_doublon_gtk.models import (  # noqa: E402
    KeepStrategy,
    RemovalReport,
    ScanOptions,
    ScanResult,
)
from py_doublon_gtk.remover import (  # noqa: E402
    GioTrashRemover,
    PermanentRemover,
    Remover,
    TrashUnsupportedError,
    remove_all,
)
from py_doublon_gtk.scanner import scan  # noqa: E402

KEEP_STRATEGIES = (KeepStrategy.OLDEST, KeepStrategy.NEWEST, KeepStrategy.SHORTEST_PATH)
KEEP_LABELS = ("Oldest file", "Newest file", "Shortest path")
MAX_LISTED_GROUPS = 200
MAX_LISTED_ERRORS = 10


def _capture[T](work: Callable[[], T]) -> Callable[[], T | Exception]:
    """Run ``work`` and *return* whatever it raises, so the UI thread can branch on it."""

    def run() -> T | Exception:
        try:
            return work()
        except Exception as error:
            return error

    return run


class PyDoublonGtkWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(
            application=application,
            title="py-doublon-gtk",
            default_width=760,
            default_height=820,
        )

        self._folder: Path | None = None
        self._result: ScanResult | None = None
        self._result_rows: list[Gtk.Widget] = []
        self._placeholder_visible = True
        self._busy = False

        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(self._build_page())
        toolbar.add_bottom_bar(self._build_action_bar())
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        self._update_sensitivity()

    # ------------------------------------------------------------------ construction

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("About py-doublon-gtk", "app.about")
        menu.append("Quit", "app.quit")
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        button.set_tooltip_text("Main menu")
        header.pack_end(button)
        return header

    def _build_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        folder_group = Adw.PreferencesGroup(title="Folder")
        self._folder_row = Adw.ActionRow(
            title="No folder selected", subtitle="Choose the folder to scan"
        )
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._on_choose_folder)
        self._folder_row.add_suffix(choose)
        self._folder_row.set_activatable_widget(choose)
        folder_group.add(self._folder_row)
        page.add(folder_group)

        options = Adw.PreferencesGroup(
            title="What to scan", description="Files are compared by content, not by name."
        )
        self._recursive_row = Adw.SwitchRow(
            title="Include subfolders", subtitle="Symbolic links are never followed", active=True
        )
        options.add(self._recursive_row)

        self._extensions_row = Adw.EntryRow(title="File types (optional)")
        self._extensions_row.set_show_apply_button(False)
        self._extensions_row.set_tooltip_text("Comma separated, for example: jpg, png. Empty = all")
        options.add(self._extensions_row)

        self._min_size_row = Adw.SpinRow(
            title="Minimum size",
            subtitle="Skip files smaller than this, in KB",
            adjustment=Gtk.Adjustment(lower=0, upper=1_000_000, step_increment=1, value=0),
            digits=0,
        )
        options.add(self._min_size_row)

        self._keep_row = Adw.ComboRow(
            title="Keep",
            subtitle="Which copy survives in each group",
            model=Gtk.StringList.new(list(KEEP_LABELS)),
            selected=0,
        )
        options.add(self._keep_row)
        page.add(options)

        deletion = Adw.PreferencesGroup(title="How to delete")
        self._trash_row = Adw.SwitchRow(
            title="Move to Trash",
            subtitle="Off: files are erased permanently and cannot be restored",
            active=True,
        )
        deletion.add(self._trash_row)
        page.add(deletion)

        self._results_group = Adw.PreferencesGroup(title="Duplicates")
        self._placeholder = Adw.ActionRow(
            title="Nothing scanned yet", subtitle="Choose a folder and press Scan"
        )
        self._results_group.add(self._placeholder)
        page.add(self._results_group)
        return page

    def _build_action_bar(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        self._spinner = Adw.Spinner(visible=False)
        box.append(self._spinner)

        self._status = Gtk.Label(
            label="", xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END
        )
        self._status.add_css_class("dim-label")
        box.append(self._status)

        self._scan_button = Gtk.Button(label="Scan")
        self._scan_button.add_css_class("suggested-action")
        self._scan_button.add_css_class("pill")
        self._scan_button.connect("clicked", self._on_scan_clicked)
        box.append(self._scan_button)

        self._delete_button = Gtk.Button(label="Delete duplicates")
        self._delete_button.add_css_class("destructive-action")
        self._delete_button.add_css_class("pill")
        self._delete_button.connect("clicked", self._on_delete_clicked)
        box.append(self._delete_button)
        return box

    # ------------------------------------------------------------------ folder picking

    def _on_choose_folder(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Select a folder to scan")
        if self._folder is not None:
            dialog.set_initial_folder(Gio.File.new_for_path(str(self._folder)))
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # The user cancelled; nothing to report.
        path = folder.get_path()
        if path is None:
            self._toast("That location is not a local folder")
            return
        self._folder = Path(path)
        self._folder_row.set_title(self._folder.name or str(self._folder))
        self._folder_row.set_subtitle(str(self._folder))
        self._clear_results("Nothing scanned yet", "Press Scan to look for duplicates")
        self._update_sensitivity()

    # ------------------------------------------------------------------ scanning

    def _on_scan_clicked(self, _button: Gtk.Button) -> None:
        if self._folder is None:
            self._toast("Choose a folder first")
            return
        try:
            options = ScanOptions(
                folder=self._folder,
                recursive=self._recursive_row.get_active(),
                extensions=split_extensions(self._extensions_row.get_text()),
                min_size=int(self._min_size_row.get_value()) * 1000,
                keep=KEEP_STRATEGIES[self._keep_row.get_selected()],
                use_trash=self._trash_row.get_active(),
            )
        except ValidationError as error:
            self._toast(clean_validation_message(error))
            return

        self._status.set_label(f"Scanning {options.folder}…")
        self._run_in_background(_capture(lambda: scan(options)), self._on_scan_done)

    def _on_scan_done(self, result: ScanResult | Exception) -> None:
        self._set_busy(False)
        if isinstance(result, Exception):
            self._status.set_label("")
            self._show_errors("Scan failed", [str(result)])
            return

        self._result = result
        self._show_results(result)
        self._status.set_label(
            f"{plural(result.files_scanned, 'file')} scanned · "
            f"{plural(result.duplicate_count, 'duplicate')} · "
            f"{format_size(result.reclaimable_bytes)} reclaimable"
        )
        if result.errors:
            self._show_errors("Some files could not be read", list(result.errors))
        self._update_sensitivity()

    def _show_results(self, result: ScanResult) -> None:
        self._clear_results(
            "No duplicates found", f"{plural(result.files_scanned, 'file')} compared"
        )
        if not result.groups:
            return

        self._results_group.remove(self._placeholder)
        self._placeholder_visible = False
        for group in result.groups[:MAX_LISTED_GROUPS]:
            row = Adw.ExpanderRow(
                title=GLib.markup_escape_text(group.keeper.name),
                subtitle=f"{plural(len(group.duplicates), 'duplicate')} · "
                f"{format_size(group.size)} each · keeping {group.keeper}",
            )
            for duplicate in group.duplicates:
                row.add_row(Adw.ActionRow(title=GLib.markup_escape_text(str(duplicate))))
            self._results_group.add(row)
            self._result_rows.append(row)

        hidden = len(result.groups) - MAX_LISTED_GROUPS
        if hidden > 0:
            more = Adw.ActionRow(title=f"… and {plural(hidden, 'more group')}")
            self._results_group.add(more)
            self._result_rows.append(more)

    def _clear_results(self, title: str, subtitle: str) -> None:
        for row in self._result_rows:
            self._results_group.remove(row)
        self._result_rows.clear()
        if not self._placeholder_visible:
            self._results_group.add(self._placeholder)
        self._placeholder_visible = True
        self._placeholder.set_title(title)
        self._placeholder.set_subtitle(subtitle)

    # ------------------------------------------------------------------ deleting

    def _on_delete_clicked(self, _button: Gtk.Button) -> None:
        result = self._result
        if result is None or not result.duplicate_count:
            return
        use_trash = self._trash_row.get_active()
        count = result.duplicate_count
        freed = format_size(result.reclaimable_bytes)

        dialog = Adw.AlertDialog(
            heading="Move duplicates to Trash?" if use_trash else "Delete duplicates for good?",
            body=(
                f"{plural(count, 'file')} will be moved to the Trash, freeing {freed}. "
                "One copy of each file is kept."
                if use_trash
                else f"{plural(count, 'file')} will be erased permanently, freeing {freed}. "
                "This cannot be undone."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Move to Trash" if use_trash else "Delete Permanently")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        # A safety decision, not a style one: the recoverable action may be the default,
        # permanent erasure may not.
        dialog.set_default_response("delete" if use_trash else "cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_confirmed, use_trash)
        dialog.present(self)

    def _on_delete_confirmed(
        self, _dialog: Adw.AlertDialog, response: str, use_trash: bool
    ) -> None:
        if response != "delete":
            return
        self._start_removal(use_trash)

    def _start_removal(self, use_trash: bool) -> None:
        result = self._result
        if result is None:
            return
        paths = result.duplicates
        remover: Remover = GioTrashRemover() if use_trash else PermanentRemover()
        self._status.set_label(f"Removing {plural(len(paths), 'file')}…")
        self._run_in_background(_capture(lambda: remove_all(paths, remover)), self._on_removal_done)

    def _on_removal_done(self, report: RemovalReport | Exception) -> None:
        self._set_busy(False)
        self._status.set_label("")

        if isinstance(report, TrashUnsupportedError):
            self._offer_permanent_deletion(report)
            return
        if isinstance(report, Exception):
            self._show_errors("Deletion failed", [str(report)])
            return

        verb = "Moved to Trash" if report.trashed else "Deleted"
        self._toast(f"{verb}: {plural(len(report.removed), 'file')}")
        self._result = None
        self._clear_results(
            "Duplicates removed", f"{plural(len(report.removed), 'file')} removed · scan again"
        )
        self._update_sensitivity()
        if report.failures:
            self._show_errors(
                "Some files could not be removed",
                [f"{failure.path}: {failure.message}" for failure in report.failures],
            )

    def _offer_permanent_deletion(self, error: TrashUnsupportedError) -> None:
        dialog = Adw.AlertDialog(
            heading="Trash is not available here",
            body=(
                f"{error}. This happens on /tmp, network shares and some removable drives. "
                "The files can still be erased permanently, which cannot be undone."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete Permanently")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_confirmed, False)
        dialog.present(self)

    # ------------------------------------------------------------------ plumbing

    def _run_in_background[T](self, work: Callable[[], T], done: Callable[[T], None]) -> None:
        def worker() -> None:
            result = work()
            # No GTK call may happen here: the worker returns data, `done` touches widgets.
            GLib.idle_add(lambda: (done(result), False)[1])

        self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._spinner.set_visible(busy)
        self._update_sensitivity()

    def _update_sensitivity(self) -> None:
        busy = self._busy
        has_duplicates = self._result is not None and self._result.duplicate_count > 0
        self._scan_button.set_sensitive(not busy and self._folder is not None)
        self._delete_button.set_sensitive(not busy and has_duplicates)

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _show_errors(self, heading: str, messages: list[str]) -> None:
        listed = messages[:MAX_LISTED_ERRORS]
        body = "\n".join(listed)
        hidden = len(messages) - len(listed)
        if hidden > 0:
            body += f"\n… and {plural(hidden, 'more')}"
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(self)
