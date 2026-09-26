import asyncio
import sys

from textual.widgets import ContentSwitcher, OptionList, RichLog, Static

import tui


def test_operation_streams_output_and_restores_controls(monkeypatch, tmp_path):
    monkeypatch.setattr(tui, "ROOT", tmp_path)
    spawn = asyncio.create_subprocess_exec
    calls = []

    async def fake_spawn(*args, **kwargs):
        calls.append(args)
        script = (
            "import os, time; "
            "os.write(1, b'[ 30%] unpack extracting\\n'); "
            "time.sleep(.1); "
            "os.write(1, b'failure: \\xe2'); "
            "time.sleep(.1); "
            "os.write(1, b'\\x86\\x92 missing image\\n'); "
            "raise SystemExit(7)"
        )
        return await spawn(sys.executable, "-u", "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    async def exercise():
        app = tui.MysticApp()
        async with app.run_test() as pilot:
            app.start_operation(["build", "sample", "firmware.zip"])
            assert app.busy
            app.action_quit()
            assert app.is_running
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.busy
            status = str(app.query_one("#status", Static).render())
            assert "exit 7" in status
            lines = app.query_one("#logs", RichLog).lines
            output = "\n".join(line.text for line in lines)
            assert "failure: → missing image" in output
            assert calls[0][-3:] == ("build", "sample", "firmware.zip")

    asyncio.run(exercise())


def test_build_validation_and_cleanup_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(tui, "ROOT", tmp_path)
    operations = []

    async def exercise():
        app = tui.MysticApp()
        monkeypatch.setattr(app, "start_operation", operations.append)
        async with app.run_test() as pilot:
            app.settings.update(name="../bad", source="missing.zip")
            app.start_build()
            assert operations == []
            app.push_screen(tui.ConfirmClean(), app.clean_confirmed)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert operations == []
            app.push_screen(tui.ConfirmClean(), app.clean_confirmed)
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            assert operations == [["clean", "--yes"]]

    asyncio.run(exercise())


def test_arrow_navigation_edits_settings_and_starts_build(monkeypatch,
                                                          tmp_path):
    monkeypatch.setattr(tui, "ROOT", tmp_path)
    (tmp_path / "firmware.zip").touch()
    operations = []

    async def exercise():
        app = tui.MysticApp()
        monkeypatch.setattr(app, "start_operation", operations.append)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("n")
            await pilot.pause()
            assert app.query_one(ContentSwitcher).current == "settings"
            await pilot.press("enter")
            await pilot.press(*"sample", "enter")
            await pilot.pause()
            assert app.settings["name"] == "sample"
            await pilot.press("down", "enter")
            await pilot.press(*"firmware.zip", "enter")
            await pilot.pause()
            await pilot.press("down", "down", "down", "down", "enter")
            await pilot.pause()
            assert app.settings["compress"]
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert operations == [["build", "sample", "firmware.zip",
                                   "--type", "auto", "--compress"]]
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one(ContentSwitcher).current == "details"
            assert app.focused is app.query_one("#builds", OptionList)

    asyncio.run(exercise())


def test_dashboard_navigation_and_selected_rebuild(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(tui, "ROOT", tmp_path)
    (tmp_path / "tmp").mkdir()
    entries = [
        {"rom_name": "pixel", "rom_type": "pixel",
         "output_path": "out/pixel/system"},
        {"rom_name": "hyperos", "rom_type": "hyperos",
         "output_path": "out/hyperos/system"},
    ]
    (tmp_path / "tmp/gsilist.json").write_text(json.dumps(entries))
    operations = []

    async def exercise():
        app = tui.MysticApp()
        monkeypatch.setattr(app, "start_operation", operations.append)
        async with app.run_test(size=(110, 30)) as pilot:
            sidebar = app.query_one("#sidebar")
            details = app.query_one("#views")
            logs = app.query_one("#logs")
            assert sidebar.region.right <= details.region.x
            assert details.region.bottom <= logs.region.y
            assert sidebar.region.height == app.query_one("#right").size.height
            await pilot.press("j")
            await pilot.pause()
            assert app.selected_index == 1
            assert "hyperos" in str(
                app.query_one("#details-text", Static).render())
            await pilot.press("enter")
            await pilot.pause()
            assert app.rebuild_name == "hyperos"
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert operations == [["rebuild", "hyperos"]]
            await pilot.press("escape", "l")
            assert app.focused is logs
            await pilot.press("tab")
            assert app.focused is app.query_one("#builds", OptionList)

    asyncio.run(exercise())


def test_debloat_browser_delete_and_save_from_history(monkeypatch, tmp_path):
    import json

    from romedit import RomEditor
    from tui_rom import DebloatScreen

    monkeypatch.setattr(tui, "ROOT", tmp_path)
    entry = {"rom_name": "sample", "rom_type": "test"}
    editor = RomEditor(tmp_path, entry)
    system = editor.tree / "system"
    (system / "app/Large").mkdir(parents=True)
    (system / "app/Small").mkdir()
    (system / "app/Large/base.apk").write_bytes(b"x" * 5000)
    (system / "app/Small/base.apk").write_bytes(b"x" * 1000)
    (system / "build.prop").write_text(
        "ro.system.build.version.sdk=35\nro.build.version.release=15\n")
    (tmp_path / "tmp/gsilist.json").write_text(json.dumps([entry]))

    async def exercise():
        app = tui.MysticApp()
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.press("d")
            await app.workers.wait_for_complete()
            await pilot.pause()
            browser = app.screen
            assert isinstance(browser, DebloatScreen)
            await pilot.press("right", "right")
            await pilot.pause()
            assert browser.paths == ["system/app/Large", "system/app/Small"]
            await pilot.press("left", "right")
            await pilot.pause()
            assert browser.paths == ["system/app/Large", "system/app/Small"]
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert not (system / "app/Large").exists()
            assert (system / "app/Small").exists()
            await pilot.press("h", "a")
            await pilot.pause()
            assert editor.read_config()["debloat"] == {
                "system": {"app": ["Large"]}}
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not browser

    asyncio.run(exercise())
