"""Keyboard-driven frontend to the CLI build pipeline."""

import asyncio
import codecs
import json
import os
from pathlib import Path
import re
import sys

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, ContentSwitcher, Footer, Input, OptionList, ProgressBar,
    RichLog, Static,
)
from textual.widgets.option_list import Option

from rich.text import Text

import make
from romedit import RomEditor
from tui_rom import DebloatScreen

ROOT = Path(__file__).resolve().parent
PROGRESS = re.compile(r"^\[\s*(\d+)%\]\s*(\S+)")
FIELDS = {
    "name": "Build name",
    "source": "Firmware path or URL",
    "rom-type": "ROM type (type:custom)",
    "tag": "Display tag",
    "key": "AVB private key path",
    "compress": "Compress ZIP",
    "keep-apps": "Keep apps",
}


class EditValue(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, label, value):
        super().__init__()
        self.label = label
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.label, markup=False)
            yield Input(value=self.value, id="edit")
            yield Static("Enter save · Esc cancel")

    def on_mount(self):
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip())

    def action_cancel(self):
        self.dismiss(None)


class ConfirmClean(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Delete everything under tmp/ and out/?")
            yield Static(
                "Includes firmware, edited trees, images and history.")
            yield OptionList("Keep files", "Delete all", id="confirmation")

    def on_mount(self):
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option_index == 1)

    def action_cancel(self):
        self.dismiss(False)


class MysticApp(App):
    TITLE = "MysticGSI"
    BINDINGS = [
        ("n", "new", "New"), ("r", "rebuild", "Rebuild"),
        ("d", "debloat", "Debloat"),
        ("l", "logs", "Logs"), ("c", "clean", "Clean all"),
        ("j", "down", "↓"), ("k", "up", "↑"),
        ("escape", "back", "Back"), ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "refresh", "Refresh"),
    ]
    CSS = """
    Screen { background: ansi_default; color: ansi_default; }
    #title {
        height: 3; padding: 0 1; border: solid ansi_default;
        text-style: bold; color: ansi_default;
    }
    #workspace { height: 1fr; }
    #sidebar {
        width: 32%; min-width: 24; border: solid ansi_default;
        border-title-color: ansi_default;
    }
    #right { width: 1fr; }
    #views {
        height: 12; border: solid ansi_default;
        border-title-color: ansi_default;
    }
    #details { padding: 0 1; }
    #details-text { height: auto; }
    #actions { height: auto; margin-top: 1; color: ansi_default; }
    #rom-actions { height: 1; }
    #rom-actions Button {
        height: 1; min-width: 16; width: auto; border: none;
        padding: 0 1; background: ansi_default; color: ansi_default;
    }
    #rom-actions Button:focus { text-style: bold reverse; }
    OptionList { border: none; background: ansi_default; padding: 0 1; }
    OptionList:focus { border: none; }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted {
        background: ansi_default; color: ansi_default;
        text-style: reverse;
    }
    #builds, #settings { height: 1fr; }
    #logs { height: 1fr; border: solid ansi_default; padding: 0 1; }
    #status { height: 1; padding: 0 1; }
    #progress { height: 1; margin: 0 1; }
    Footer, FooterKey,
    FooterKey > .footer-key--key,
    FooterKey > .footer-key--description {
        background: ansi_default; color: ansi_default;
    }
    Input, Input:focus {
        background: ansi_default; color: ansi_default;
        border: solid ansi_default;
    }
    ModalScreen { align: center middle; }
    .dialog {
        width: 64; max-width: 95%; height: auto;
        padding: 1 2; border: solid ansi_default; background: ansi_default;
    }
    .dialog Static { height: auto; margin-bottom: 1; }
    .dialog OptionList { height: 4; }
    .dialog Input { margin-bottom: 1; }
    """

    def __init__(self):
        super().__init__(ansi_color=True)
        self.theme = "textual-ansi"
        self.busy = False
        self.entries = []
        self.settings = dict.fromkeys(FIELDS, "")
        self.settings.update({"rom-type": "auto", "compress": False,
                              "keep-apps": False})
        self.rebuild_name = None
        self.selected_index = None
        self.active_name = None
        self.operation_status = ""

    def compose(self) -> ComposeResult:
        yield Static("MYSTICGSI BUILD MANAGER", id="title")
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield OptionList(id="builds")
            with Vertical(id="right"):
                with ContentSwitcher(initial="details", id="views"):
                    with VerticalScroll(id="details"):
                        yield Static(id="details-text", markup=False)
                        yield Static(
                            "[R] Rebuild image   [N] New build\n"
                            "[L] Focus logs      [C] Clean all files",
                            id="actions", markup=False)
                        with Horizontal(id="rom-actions"):
                            yield Button("[D] Debloat", id="debloat")
                    yield OptionList(id="settings")
                yield RichLog(id="logs", highlight=False, markup=False,
                              wrap=True, max_lines=10000)
        yield Static("Ready", id="status", markup=False)
        yield ProgressBar(total=100, show_eta=False, id="progress")
        yield Footer()

    def on_mount(self):
        self.query_one("#sidebar").border_title = "BUILDS · [N] New"
        self.query_one("#views").border_title = "BUILD DETAILS"
        self.query_one("#logs").border_title = "CONSOLE OUTPUT"
        self.action_refresh()
        self.query_one("#builds").focus()

    def action_back(self):
        self.query_one(ContentSwitcher).current = "details"
        self.update_details()
        self.query_one("#builds").focus()

    def action_down(self):
        if isinstance(self.focused, OptionList):
            self.focused.action_cursor_down()
        else:
            self.query_one("#logs", RichLog).scroll_down()

    def action_up(self):
        if isinstance(self.focused, OptionList):
            self.focused.action_cursor_up()
        else:
            self.query_one("#logs", RichLog).scroll_up()

    def action_logs(self):
        self.query_one("#logs").focus()

    def action_new(self):
        if isinstance(self.screen, ModalScreen):
            return
        if self.busy:
            self.notify("An operation is running.")
            return
        self.rebuild_name = None
        self.open_settings()

    def action_rebuild(self):
        if isinstance(self.screen, ModalScreen):
            return
        if self.busy or self.selected_index is None:
            return
        self.rebuild_name = self.entries[self.selected_index]["rom_name"]
        self.open_settings()

    def selected_editor(self):
        if isinstance(self.screen, ModalScreen):
            return None
        if self.busy or self.selected_index is None:
            self.notify("Select a finished build first.")
            return None
        return RomEditor(ROOT, self.entries[self.selected_index])

    def action_debloat(self):
        try:
            editor = self.selected_editor()
            if editor:
                self.push_screen(DebloatScreen(editor))
        except (OSError, ValueError) as error:
            self.notify(str(error), severity="error")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "debloat":
            self.action_debloat()

    def open_settings(self):
        self.refresh_settings()
        self.query_one(ContentSwitcher).current = "settings"
        self.query_one("#views").border_title = (
            f"REBUILD: {self.rebuild_name}" if self.rebuild_name else
            "NEW BUILD · Enter edit / toggle")
        self.query_one("#settings", OptionList).highlighted = 0
        self.query_one("#settings").focus()

    def action_clean(self):
        if isinstance(self.screen, ModalScreen):
            return
        if not self.busy:
            self.push_screen(ConfirmClean(), self.clean_confirmed)

    def entry_prompt(self, entry):
        name = str(entry.get("rom_name", "?"))
        path = ROOT / (str(entry.get("output_path", "")) + ".img")
        try:
            size = make.bytes_to_human(path.stat().st_size)
            status = "Ready"
        except OSError:
            size, status = "—", "Missing image"
        if name == self.active_name:
            status = self.operation_status
        return Text(f"{name}\n  Status: {status}\n  Size: {size}\n")

    def action_refresh(self):
        listing = self.query_one("#builds", OptionList)
        selected = listing.highlighted or 0
        try:
            entries = json.loads((ROOT / "tmp/gsilist.json").read_text())
            if not isinstance(entries, list):
                raise ValueError("Build history must be a list")
            self.entries = [e for e in entries if isinstance(e, dict)
                            and isinstance(e.get("rom_name"), str)]
        except FileNotFoundError:
            self.entries = []
        except (OSError, ValueError) as error:
            self.entries = []
            self.notify(f"Cannot read build history: {error}",
                        severity="error")
        if self.active_name and not any(
                e["rom_name"] == self.active_name for e in self.entries):
            self.entries.append({"rom_name": self.active_name,
                                 "rom_type": self.value("rom-type")})
        listing.clear_options()
        for index, entry in enumerate(self.entries):
            listing.add_option(Option(self.entry_prompt(entry), id=str(index)))
        self.selected_index = (min(selected, len(self.entries) - 1)
                               if self.entries else None)
        if self.selected_index is not None:
            listing.highlighted = self.selected_index
        self.update_details()

    def update_details(self):
        if self.query_one(ContentSwitcher).current == "details":
            self.query_one("#views").border_title = "BUILD DETAILS"
        if self.selected_index is None:
            text = "No recorded builds.\n\nPress N to create a build."
        else:
            entry = self.entries[self.selected_index]
            name = entry["rom_name"]
            output_path = entry.get("output_path")
            path = (str(output_path) + ".img" if output_path
                    else "Pending")
            tree = Path("tmp") / name / "images/system"
            text = (f"{name}\n\n"
                    f"ROM type: {entry.get('rom_type', 'unknown')}\n"
                    f"Variant: {entry.get('variant_tag') or 'default'}\n"
                    f"Image: {path}\n"
                    f"System tree: {tree}")
        self.query_one("#details-text", Static).update(text)

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == "builds":
            index = event.option_index
            if index < len(self.entries):
                self.selected_index = index
                self.update_details()

    def refresh_settings(self):
        listing = self.query_one("#settings", OptionList)
        highlighted = listing.highlighted or 0
        listing.clear_options()
        fields = ("compress", "key") if self.rebuild_name else FIELDS
        for key in fields:
            value = self.settings[key]
            if isinstance(value, bool):
                value = "yes" if value else "no"
            listing.add_option(Option(
                Text(f"{FIELDS[key]:<27} {value or '(default)'!s}"),
                id=key))
        action = (f"Rebuild {self.rebuild_name}" if self.rebuild_name else
                  "Start build")
        listing.add_option(Option(action, id="start"))
        listing.highlighted = min(highlighted, listing.option_count - 1)

    def on_option_list_option_selected(self, event):
        key = event.option.id
        if event.option_list.id == "builds":
            self.action_rebuild()
        elif event.option_list.id == "settings" and not self.busy:
            if key == "start":
                if self.rebuild_name:
                    self.start_operation(["rebuild", self.rebuild_name,
                                          *self.output_options()])
                else:
                    self.start_build()
            elif isinstance(self.settings[key], bool):
                self.settings[key] = not self.settings[key]
                self.refresh_settings()
            else:
                def save(value):
                    if value is not None:
                        self.settings[key] = value
                        self.refresh_settings()
                self.push_screen(EditValue(FIELDS[key], self.settings[key]),
                                 save)

    def value(self, key):
        return str(self.settings[key]).strip()

    def output_options(self):
        options = ["--compress"] if self.settings["compress"] else []
        if self.value("key"):
            options.extend(["--avb-key",
                            os.path.expanduser(self.value("key"))])
        return options

    def clean_confirmed(self, confirmed):
        if confirmed:
            self.start_operation(["clean", "--yes"])

    def start_build(self):
        name, source = self.value("name"), self.value("source")
        rom_type = self.value("rom-type") or "auto"
        try:
            make.safe_name(name, "build name")
            base, _, custom = rom_type.partition(":")
            make.safe_name(base, "ROM type")
            make.safe_name(custom or "default", "custom ROM type")
            if not source:
                raise ValueError("Enter a firmware path or URL")
            if "://" not in source:
                source = os.path.expanduser(source)
                if not (ROOT / source).is_file():
                    raise ValueError(f"No such firmware file: {source}")
        except ValueError as error:
            self.notify(str(error), severity="error")
            return
        args = ["build", name, source, "--type", rom_type]
        if self.value("tag"):
            args.extend(["--add", self.value("tag")])
        if self.settings["keep-apps"]:
            args.append("--no-debloat")
        self.start_operation([*args, *self.output_options()])

    def start_operation(self, args):
        if self.busy:
            return
        self.busy = True
        self.active_name = (args[1] if args[0] in ("build", "rebuild")
                            else None)
        self.operation_status = f"Running {args[0]}"
        self.action_refresh()
        if self.active_name:
            index = next(i for i, e in enumerate(self.entries)
                         if e["rom_name"] == self.active_name)
            self.query_one("#builds", OptionList).highlighted = index
            self.selected_index = index
        self.query_one(ContentSwitcher).current = "details"
        self.update_details()
        self.action_logs()
        self.query_one("#logs", RichLog).clear()
        self.query_one("#logs").border_title = (
            f"CONSOLE · {args[0]} {self.active_name or ''}".rstrip())
        self.query_one("#progress", ProgressBar).update(progress=0)
        self.query_one("#status", Static).update(f"Running {args[0]}…")
        self.run_operation(args)

    def show_line(self, line):
        self.query_one("#logs", RichLog).write(line)
        match = PROGRESS.match(line)
        if match:
            self.query_one("#progress", ProgressBar).update(
                progress=min(int(match[1]), 100))
            self.query_one("#status", Static).update(match[2])
            self.operation_status = f"{match[2]} ({match[1]}%)"
            for index, entry in enumerate(self.entries):
                if entry["rom_name"] == self.active_name:
                    listing = self.query_one("#builds", OptionList)
                    listing.replace_option_prompt(str(index),
                                                  self.entry_prompt(entry))

    @work
    async def run_operation(self, args):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-u", str(ROOT / "cli.py"), *args,
                cwd=ROOT, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            pending = ""
            while chunk := await process.stdout.read(4096):
                pending += decoder.decode(chunk).replace("\r", "\n")
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    if line:
                        self.show_line(line)
                if len(pending) > 65536:
                    self.show_line(pending)
                    pending = ""
            pending += decoder.decode(b"", final=True)
            if pending:
                self.show_line(pending)
            code = await process.wait()
            result = ("Completed" if code == 0 else
                      f"Failed (exit {code}) — see log")
            self.operation_status = result
            self.query_one("#status", Static).update(result)
            self.notify(result, severity="information" if code == 0
                        else "error")
        except OSError as error:
            self.operation_status = "Failed to start"
            self.show_line(str(error))
            self.query_one("#status", Static).update("Failed to start")
        finally:
            if process is not None and process.returncode is None:
                await process.wait()
            self.busy = False
            self.action_refresh()

    def action_quit(self):
        editing = any(isinstance(screen, DebloatScreen) and screen.loading
                      for screen in self.screen_stack)
        if self.busy or editing:
            self.notify("Wait for the operation to finish before quitting.",
                        severity="warning")
        else:
            self.exit()


if __name__ == "__main__":
    MysticApp().run()
