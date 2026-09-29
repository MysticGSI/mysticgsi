import json
import os
import shutil
from pathlib import Path

import pytest

import fsops
from make import RomPorter, SettingsProp


def test_rom_detection_uses_honor_partition_and_respects_precedence(tmp_path):
    system = tmp_path / "system"
    product = tmp_path / "product"
    honor_product = tmp_path / "product_h"
    (system / "system").mkdir(parents=True)
    product.mkdir()
    (honor_product / "etc/prop").mkdir(parents=True)
    (system / "system/build.prop").write_text(
        "ro.product.system.brand=Honor\n"
    )
    (product / "build.prop").write_text("")
    (honor_product / "etc/prop/local.prop").write_text(
        "ro.product.board=PTP\n"
        "ro.product.brand=HONOR\n"
        "ro.comp.hl.product_base_version="
        "PTP-LGRP2-OVS 9.0.0.99(SP86log)\n"
    )

    porter = RomPorter("test")
    porter.partition_dirs = {
        "system": str(system),
        "product": str(product),
        "product_h": str(honor_product),
    }

    porter._detect_rom_type()
    assert porter.rom_type == "magicos"

    porter._patch_huawei("HONOR", "device", "unknown", "magic", str(system))
    assert porter._get_board() == "PTP"

    porter.rom_type = "auto"
    porter._get_partition_prop("system").values[
        "ro.product.system.brand"
    ] = "other"
    porter._detect_rom_type()
    assert porter.rom_type == "magicos"

    porter.rom_type = "auto"
    porter._get_partition_prop("system").values[
        "ro.lineage.build.version"
    ] = "22"
    porter._detect_rom_type()
    assert porter.rom_type == "lineageOS"

    porter.rom_type = "auto"
    porter.override_rom_type = "pixel"
    porter._detect_rom_type()
    assert porter.rom_type == "pixel"


def test_missing_vndks_merge_without_replacing_stock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    system = tmp_path / "system"
    (system / "apex").mkdir(parents=True)
    (system / "apex/stock.apex").write_bytes(b"stock")
    patches = tmp_path / "patches/vndk/32"
    (patches / "apex").mkdir(parents=True)
    (patches / "apex/stock.apex").write_bytes(b"replacement")
    (patches / "apex/missing.apex").write_bytes(b"missing")
    (patches / "etc/nested").mkdir(parents=True)
    (patches / "etc/nested/libraries.txt").write_text("libraries")
    (system / "etc/nested").mkdir(parents=True)
    (system / "etc/nested/stock.txt").write_text("stock")
    prop = SettingsProp()
    prop.values = {
        "ro.system.build.version.sdk": "32",
        "ro.build.version.release": "12.1",
    }
    porter = RomPorter("test")
    monkeypatch.setattr(porter, "_get_system_root", lambda: str(system))
    monkeypatch.setattr(porter, "_get_partition_prop", lambda part: prop)

    porter._copy_missing_vndks()

    assert (system / "apex/stock.apex").read_bytes() == b"stock"
    assert (system / "apex/missing.apex").read_bytes() == b"missing"
    assert (system / "etc/nested/libraries.txt").read_text() == "libraries"
    assert (system / "etc/nested/stock.txt").read_text() == "stock"


def test_dotted_release_selects_sdk_and_major_patches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    system = tmp_path / "system"
    product = tmp_path / "product"
    system.mkdir()
    product.mkdir()
    system_prop = SettingsProp()
    system_prop.values = {
        "ro.system.build.version.sdk": "32",
        "ro.build.version.release": "12.1",
    }
    system_prop.path = str(system / "build.prop")
    Path(system_prop.path).write_text("stock=true\n")
    product_prop = SettingsProp()
    product_prop.path = str(product / "build.prop")
    Path(product_prop.path).write_text("stock=true\n")
    generic = tmp_path / "patches/all/12"
    generic.mkdir(parents=True)
    (generic / "system.prop").write_text("generic=true\n")
    specific = tmp_path / "patches/32/testrom"
    specific.mkdir(parents=True)
    (specific / "system.prop").write_text("specific=true\n")
    (specific / "config.json").write_text(
        json.dumps({"no_device_overlays": True, "use_stock_init": True})
    )
    porter = RomPorter("test")
    porter.rom_type = "testrom"
    porter.android_version = system_prop.get_android_version()
    porter.partition_dirs = {"system": str(system), "product": str(product)}
    props = {"system": system_prop, "product": product_prop}
    monkeypatch.setattr(porter, "_get_partition_prop", props.get)
    monkeypatch.setattr(porter, "_get_system_root", lambda: str(system))

    assert porter.android_version == "12.1"
    assert porter._is_android_version(12)
    assert not porter._is_android_version(13)
    assert porter._is_android_at_least(12)
    assert not porter._is_android_at_least(13)
    porter._apply_generic_patches()
    porter._apply_rom_patches()

    assert Path(system_prop.path).read_text() == (
        "stock=true\ngeneric=true\nspecific=true\n"
    )


@pytest.mark.parametrize("failure", ["decode", "patch", "build", "empty"])
def test_framework_failure_preserves_stock(tmp_path, monkeypatch, failure):
    framework = tmp_path / "services.jar"
    framework.write_bytes(b"stock framework")
    config = tmp_path / "patches.json"
    config.write_text(json.dumps({"system": {"services.jar": ["required"]}}))
    porter = RomPorter("test")
    porter.partition_dirs = {"system": str(tmp_path)}
    stages = []

    def run(argv, *, cwd=None, stdin=None):
        stage = (
            "patch" if argv[0] == "patch" else ("decode" if argv[1] == "d" else "build")
        )
        stages.append(stage)
        if stage == "build":
            Path(argv[argv.index("-o") + 1]).write_bytes(
                b"" if failure == "empty" else b"partial framework"
            )
        return 1 if stage == failure else 0

    monkeypatch.setattr(fsops, "run", run)

    with pytest.raises(RuntimeError):
        porter._apply_framework_patches(str(config), str(tmp_path))

    assert framework.read_bytes() == b"stock framework"
    if failure == "patch":
        assert stages == ["decode", "patch"]
    assert not list(tmp_path.glob("framework-*"))


def test_framework_success_replaces_stock_atomically(tmp_path, monkeypatch):
    framework = tmp_path / "services.jar"
    framework.write_bytes(b"stock framework")
    framework.chmod(0o640)
    porter = RomPorter("test")

    def run(argv, *, cwd=None, stdin=None):
        if argv[0] == "apktool" and argv[1] == "b":
            assert framework.read_bytes() == b"stock framework"
            Path(argv[argv.index("-o") + 1]).write_bytes(b"patched framework")
        return 0

    monkeypatch.setattr(fsops, "run", run)
    porter._patch_framework(
        str(framework),
        "services.jar",
        ["required"],
        str(tmp_path),
        str(tmp_path / "decoded"),
    )

    assert framework.read_bytes() == b"patched framework"
    assert os.stat(framework).st_mode & 0o777 == 0o640


def test_rom_patch_errors_propagate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "patches/32/testrom"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"no_device_overlays": True, "use_stock_init": True})
    )
    (config_dir / "patches.json").write_text("{}")
    prop = SettingsProp()
    prop.values = {
        "ro.system.build.version.sdk": "32",
        "ro.build.version.release": "12.1",
    }
    porter = RomPorter("test")
    porter.rom_type = "testrom"
    porter.partition_dirs = {"system": str(tmp_path), "product": str(tmp_path)}
    monkeypatch.setattr(porter, "_get_partition_prop", lambda part: prop)
    monkeypatch.setattr(porter, "_get_system_root", lambda: str(tmp_path))

    def fail(*args):
        raise RuntimeError("required patch failed")

    monkeypatch.setattr(porter, "_apply_framework_patches", fail)
    with pytest.raises(RuntimeError, match="required patch failed"):
        porter._apply_rom_patches()


def test_alos_repair_patch_preempts_vpd_wait(tmp_path):
    patch = shutil.which("gpatch") or shutil.which("patch")
    if not patch:
        pytest.skip("patch command unavailable")

    relative = Path(
        "smali/com/android/server/desktop/repairmode/DesktopRepairModeService.smali"
    )
    smali = tmp_path / relative
    smali.parent.mkdir(parents=True)
    smali.write_text(
        ".end method\n\n"
        ".method private final readPostManufacturingConfig()"
        "Lcom/google/android/factory/base/proto/postmanufacturing/Config;\n"
        "    .locals 8\n\n"
        "    const-string v0, "
        '"vendor.google.desktop.vpd_executor.IVpdExecutor/default"\n\n'
        "    invoke-static {v0}, "
        "Landroid/os/ServiceManager;->waitForService"
        "(Ljava/lang/String;)Landroid/os/IBinder;\n"
        ".end method\n"
    )
    patch_file = (
        Path(__file__).resolve().parents[1]
        / "patches/37/alos"
        / "framework-patches/desktop_repair_mode.patch"
    )

    assert (
        fsops.run([patch, "-p0", "-F", "0", "-s", "-N"], cwd=tmp_path, stdin=patch_file)
        == 0
    )
    method = smali.read_text().split(".method private final ", 1)[1]
    assert method.index("return-object v0") < method.index("waitForService")
