import json

import pytest

import buildlock
import fsops
from make import RomPorter, SettingsProp
from romedit import RomEditor


def create_editor(tmp_path):
    editor = RomEditor(tmp_path, {"rom_name": "sample", "rom_type": "test"})
    system = editor.tree / "system"
    system.mkdir(parents=True)
    (system / "build.prop").write_text(
        "ro.system.build.version.sdk=35\nro.build.version.release=15\n")
    return editor


def test_sizes_and_removal_do_not_follow_symlinks(tmp_path):
    editor = create_editor(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"x" * 9999)
    folder = editor.tree / "system/app/Large"
    folder.mkdir(parents=True)
    (folder / "base.apk").write_bytes(b"x" * 256)
    link = editor.tree / "outside"
    link.symlink_to(outside)
    sizes = editor.scan()
    assert sizes["system/app/Large"] == 256
    assert sizes["outside"] == link.lstat().st_size
    assert "outside/keep" not in sizes
    with pytest.raises(ValueError, match="leaves"):
        editor.remove("outside/keep")
    with pytest.raises(ValueError):
        editor.remove("../outside")
    with pytest.raises(ValueError, match="root"):
        editor.remove(".")
    editor.remove("outside")
    assert (outside / "keep").is_file()
    assert not link.is_symlink()


def test_remove_then_remember_maps_merged_partitions_to_future_patches(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    editor = create_editor(tmp_path)
    app = editor.tree / "system/product/app/Example[1]"
    app.mkdir(parents=True)
    (app / "base.apk").write_bytes(b"app")
    sibling = app.with_name("Example1")
    sibling.mkdir()
    (sibling / "base.apk").write_bytes(b"keep")
    config = {"use_stock_init": True, "no_device_overlays": True}
    editor.save_config(config)
    editor.remove("system/product/app/Example[1]")
    assert not app.exists()
    assert sibling.exists()
    reopened = RomEditor(tmp_path, {"rom_name": "sample", "rom_type": "test"})
    assert reopened.history() == ["system/product/app/Example[1]"]
    reopened.remember(reopened.history()[0])
    reopened.remember(reopened.history()[0])
    saved = reopened.read_config()
    assert saved["use_stock_init"]
    assert saved["debloat"] == {"product": {"app": ["Example[[]1]"]}}

    app.mkdir()
    (app / "base.apk").write_bytes(b"new firmware")
    system_prop = SettingsProp()
    system_prop.init_from_file(str(editor.tree / "system/build.prop"))
    porter = RomPorter("sample")
    porter.rom_type = "test"
    porter.partition_dirs = {
        "system": str(editor.tree), "product": str(app.parent.parent)}
    monkeypatch.setattr(porter, "_get_partition_prop",
                        lambda part: system_prop)
    porter._apply_rom_patches()
    assert not app.exists()
    assert sibling.exists()


def test_edit_lock_and_failed_deletion_preserve_history(tmp_path, monkeypatch):
    editor = create_editor(tmp_path)
    file = editor.tree / "system/app.apk"
    file.write_bytes(b"app")
    with buildlock.hold(path=tmp_path / buildlock.LOCK_PATH):
        with pytest.raises(RuntimeError, match="running"):
            editor.remove("system/app.apk")
    monkeypatch.setattr(fsops, "rmrf", lambda path: None)
    with pytest.raises(OSError, match="completely"):
        editor.remove("system/app.apk")
    assert editor.history() == []
    assert file.exists()


def test_config_validation_and_preview_selection(tmp_path):
    editor = create_editor(tmp_path)
    prop = editor.tree / "system/build.prop"
    prop.write_text(
        "ro.system.build.version.sdk=36\nro.build.version.release=16\n"
        "ro.build.version.codename=Baklava\n"
        "ro.build.version.known_codenames=Baklava\n")
    assert editor.config_path() == (
        tmp_path / "patches/Baklava/test/config.json")
    editor.save_config({"debloat": {"system": {"app": ["Test*"]}}})
    before = editor.config_path().read_bytes()
    for config in ([], {"debloat": []},
                   {"debloat": {"system": {"../escape": ["app"]}}},
                   {"debloat": {"system": {"app": ["/absolute"]}}}):
        with pytest.raises(ValueError):
            editor.save_config(config)
        assert editor.config_path().read_bytes() == before
    assert json.loads(before)["debloat"]["system"]["app"] == ["Test*"]
