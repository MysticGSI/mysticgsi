"""Keyboard-driven ROM tree browser and patch configuration editor."""

import asyncio
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

import make


class ConfirmRemoval(ModalScreen[str]):
    BINDINGS = [("k,escape", "cancel", "Keep"),
                ("d", "delete_only", "Delete only"),
                ("a", "delete_and_save", "Delete + debloat")]

    def __init__(self, relative):
        super().__init__()
        self.relative = relative

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"Delete {self.relative}?", markup=False)
            yield Static("Deletion is permanent. Rebuild to update the image.")
            yield OptionList(
                Option(Text("[K] Keep"), id="cancel"),
                Option(Text("[D] Delete from this tree"), id="delete"),
                Option(Text("[A] Delete + add to patch set's debloat list"),
                       id="save"))

    def on_mount(self):
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)

    def action_cancel(self):
        self.dismiss("cancel")

    def action_delete_only(self):
        self.dismiss("delete")

    def action_delete_and_save(self):
        self.dismiss("save")


class DebloatScreen(ModalScreen):
    BINDINGS = [
        ("d,delete", "delete", "Delete"),
        ("a", "remember", "Add to debloat"),
        ("h", "history", "Removed paths"),
        ("left,backspace", "parent", "Parent"),
        ("right", "open", "Open"),
        ("j", "down", "↓"), ("k", "up", "↑"),
        ("escape,q", "close", "Back"),
    ]
    DEFAULT_CSS = """
    DebloatScreen { background: ansi_default; color: ansi_default; }
    #browser-title {
        height: auto; padding: 0 1; border: solid ansi_default;
        color: ansi_default;
    }
    #files { height: 1fr; border: solid ansi_default; }
    #browser-status { height: 2; padding: 0 1; }
    ConfirmRemoval .dialog OptionList { height: 6; }
    """

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.current = Path(".")
        self.sizes = {}
        self.removed = False
        self.loading = False
        self.paths = []
        self.cursor_index = None

    def compose(self) -> ComposeResult:
        yield Static(id="browser-title", markup=False)
        yield OptionList(id="files")
        yield Static(id="browser-status", markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one(OptionList).focus()
        self.scan()

    @work
    async def scan(self):
        self.loading = True
        self.query_one("#browser-status", Static).update(
            "Calculating apparent sizes…")
        try:
            self.sizes = await asyncio.to_thread(self.editor.scan)
            self.refresh_files()
        except (OSError, ValueError, RuntimeError) as error:
            self.query_one("#browser-status", Static).update(str(error))
        finally:
            self.loading = False

    def refresh_files(self, reset=False):
        listing = self.query_one("#files", OptionList)
        previous = listing.highlighted or 0
        self.cursor_index = None
        listing.clear_options()
        if self.removed:
            self.paths = self.editor.history()
            title = "REMOVED PATHS · A adds the selected path to debloat"
        else:
            self.paths = sorted(
                (p for p in self.sizes if p != "."
                 and Path(p).parent == self.current),
                key=lambda p: (-self.sizes[p], p.casefold(), p))
            title = f"DEBLOAT · {self.editor.name} / {self.current}"
            if self.current != Path("."):
                listing.add_option(Option(Text("           .. /"),
                                          id="parent"))
            for index, relative in enumerate(self.paths):
                path = self.editor.tree / relative
                suffix = "@" if path.is_symlink() else (
                    "/" if path.is_dir() else "")
                size = make.bytes_to_human(self.sizes[relative])
                listing.add_option(Option(Text(
                    f"  {size:>12}  {path.name}{suffix}"), id=str(index)))
        if self.removed:
            for index, relative in enumerate(self.paths):
                listing.add_option(Option(Text(f"  {relative}"),
                                          id=str(index)))
        if listing.option_count:
            if reset:
                previous = (1 if not self.removed and self.paths
                            and self.current != Path(".") else 0)
            listing.highlighted = min(previous, listing.option_count - 1)
        self.update_cursor()
        self.query_one("#browser-title", Static).update(title)
        total = make.bytes_to_human(self.sizes.get(str(self.current), 0))
        self.query_one("#browser-status", Static).update(
            f"{len(self.paths)} entries · {total} apparent size · "
            "largest first\n"
            "→ / Enter open · ← parent · D delete · A save rule · H history")

    def update_cursor(self):
        listing = self.query_one("#files", OptionList)
        current = listing.highlighted
        for index in {self.cursor_index, current}:
            if index is None or index >= listing.option_count:
                continue
            option = listing.get_option_at_index(index)
            marker = Text("> " if index == current else "  ")
            prompt = marker + option.prompt[2:]
            listing.replace_option_prompt(option.id, prompt)
        self.cursor_index = current

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == "files":
            self.update_cursor()

    def selected_path(self):
        listing = self.query_one("#files", OptionList)
        if listing.highlighted is None:
            return None
        option = listing.get_option_at_index(listing.highlighted)
        if option.id == "parent":
            return None
        return self.paths[int(option.id)]

    def on_option_list_option_selected(self, event):
        if self.loading or self.removed:
            return
        if event.option.id == "parent":
            self.action_parent()
            return
        self.action_open()

    def action_open(self):
        if self.loading or self.removed:
            return
        relative = self.selected_path()
        if relative is None:
            return
        path = self.editor.checked_path(relative)
        if path.is_dir() and not path.is_symlink():
            self.current = Path(relative)
            self.refresh_files(reset=True)

    def action_parent(self):
        if not self.loading:
            self.removed = False
            self.current = self.current.parent
            self.refresh_files(reset=True)

    def action_down(self):
        self.query_one(OptionList).action_cursor_down()

    def action_up(self):
        self.query_one(OptionList).action_cursor_up()

    def action_delete(self):
        relative = self.selected_path()
        if relative is not None and not self.loading and not self.removed:
            def confirmed(action):
                if action != "cancel":
                    self.delete_path(relative, action == "save")
            self.app.push_screen(ConfirmRemoval(relative), confirmed)

    @work
    async def delete_path(self, relative, remember):
        self.loading = True
        try:
            await asyncio.to_thread(self.editor.remove, relative, remember)
            self.sizes = await asyncio.to_thread(self.editor.scan)
            self.refresh_files()
            self.notify("Deleted. Rebuild to update the image.")
        except (OSError, ValueError, RuntimeError) as error:
            self.notify(str(error), severity="error")
        finally:
            self.loading = False

    def action_remember(self):
        relative = self.selected_path()
        if relative is None or self.loading:
            return
        try:
            self.editor.remember(relative)
            self.notify(f"Added to {self.editor.config_path()}")
        except (OSError, ValueError, RuntimeError) as error:
            self.notify(str(error), severity="error")

    def action_history(self):
        if self.loading:
            return
        try:
            self.removed = not self.removed
            self.refresh_files()
        except (OSError, ValueError) as error:
            self.notify(str(error), severity="error")

    def action_close(self):
        if not self.loading:
            self.dismiss()
