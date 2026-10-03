import json
import os
import re

import fsops
from assets import ensure_extracted, is_packed

from . import vendors
from .binary import patch_init
from .framework import apply_framework_patches


def apply_generic_patches(ctx):
    system_prop = ctx.partition_prop("system")
    android_version = str(system_prop.get_android_version())
    patch_version = android_version.split(".", 1)[0]
    patch_path = os.path.join(ctx.patches_dir, "all", patch_version)

    if not os.path.exists(patch_path):
        return

    ensure_extracted(patch_path, ctx.log)

    system = ctx.system_root()
    system_ext = ctx.partition_dirs.get("system_ext")
    system_prop_path = ctx.partition_prop("system").path
    product_prop_path = ctx.partition_prop("product").path

    if os.path.exists(os.path.join(patch_path, "system.prop")):
        fsops.append_file(
            os.path.join(patch_path, "system.prop"), system_prop_path
        )

    if os.path.exists(os.path.join(patch_path, "product.prop")):
        fsops.append_file(
            os.path.join(patch_path, "product.prop"), product_prop_path
        )

    fsops.cp_r(f"{patch_path}/system", f"{system}/")
    if system_ext and os.path.exists(os.path.join(patch_path, "system_ext")):
        fsops.cp_r(f"{patch_path}/system_ext", f"{system_ext}/../")

    if os.path.exists(os.path.join(patch_path, "file_contexts")):
        fsops.append_file(
            os.path.join(patch_path, "file_contexts"),
            os.path.join(system, "etc", "selinux", "plat_file_contexts"),
        )


def copy_missing_vndks(ctx):
    system_ext = ctx.partition_dirs.get("system_ext")
    system = ctx.system_root()
    android_sdk = str(ctx.partition_prop("system").get_sdk_version())
    system_prop = ctx.partition_prop("system")
    android_version = str(system_prop.get_android_version())
    if system_ext and os.path.exists(os.path.join(system_ext, "apex")):
        fsops.cp_r(os.path.join(system_ext, "apex"), system)
        fsops.rmrf(os.path.join(system_ext, "apex"))

    # Preview builds report a codename (e.g. VanillaIceCream) instead of
    # a version number, so try that before the SDK level.
    vndk_path = os.path.join(ctx.patches_dir, "vndk", android_version)
    if not os.path.exists(vndk_path):
        vndk_path = os.path.join(ctx.patches_dir, "vndk", android_sdk)

    if os.path.exists(vndk_path):
        ensure_extracted(vndk_path, ctx.log)
        fsops.cp_r(
            f"{vndk_path}/*",
            system,
            clobber=False,
            exclude=is_packed,
        )


def apply_rom_patches(ctx):
    vendors.detect_rom_type(ctx)

    system_prop = ctx.partition_prop("system")
    android_version = str(system_prop.get_android_version())
    android_sdk = str(system_prop.get_sdk_version())
    system = ctx.system_root()
    product_prop = ctx.partition_prop("product")
    system_ext_prop = ctx.partition_prop("system_ext")
    system_ext = ctx.partition_dirs.get("system_ext")
    product = ctx.partition_dirs["product"]
    patch_path = os.path.join(ctx.patches_dir, android_sdk)

    rom_patches_dir = os.path.join(ctx.patches_dir, android_sdk, ctx.rom_type)
    if not re.fullmatch(r"\d+(?:\.\d+)*", android_version):
        rom_patches_dir = os.path.join(
            ctx.patches_dir, android_version, ctx.rom_type
        )

    config = {}

    if not os.path.exists(rom_patches_dir):
        return

    config_path = os.path.join(rom_patches_dir, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception as e:
            ctx.log(f"Error reading {config_path}: {e}")

    if not config.get("no_device_overlays", False):
        overlay_dir = os.path.join(
            ctx.patches_dir,
            "all",
            android_version.split(".", 1)[0],
            "device_overlay",
        )
        overlay_dst = os.path.join(product, "overlay")
        fsops.mkdirp(overlay_dst)
        ensure_extracted(overlay_dir, ctx.log)
        fsops.cp_r(f"{overlay_dir}/*", overlay_dst)

    if not config.get("use_stock_init", False):
        init_dir = os.path.join(patch_path, "init")
        if os.path.exists(init_dir):
            fsops.cp_r(f"{init_dir}/*", f"{system}/")
        else:
            ctx.log(f"No init for {android_version}; patching stock init")
            patch_init(ctx)
    elif ctx.rom_type in ("magicos", "emui", "harmonyos", "pixel"):
        patch_init(ctx)

    system_prop_file = os.path.join(rom_patches_dir, "system.prop")
    if os.path.exists(system_prop_file):
        fsops.append_file(system_prop_file, system_prop.path)

    product_prop_file = os.path.join(rom_patches_dir, "product.prop")
    if os.path.exists(product_prop_file) and product_prop:
        fsops.append_file(product_prop_file, product_prop.path)

    system_ext_prop_file = os.path.join(rom_patches_dir, "system_ext.prop")
    if system_ext and system_ext_prop and os.path.exists(system_ext_prop_file):
        fsops.append_file(system_ext_prop_file, system_ext_prop.path)

    debloatware = (config.get("debloat") or {}) if ctx.debloat else {}
    for partition, folders in debloatware.items():
        if partition not in ctx.partition_dirs:
            continue
        if partition == "system":
            partition_path = system
        else:
            partition_path = ctx.partition_dirs[partition]
        for folder, apps in folders.items():
            for app in apps:
                fsops.rmrf(os.path.join(partition_path, folder, app))

    for partition in ctx.partition_dirs:
        src = os.path.join(rom_patches_dir, partition)
        if os.path.exists(src):
            if partition == "system":
                dst = system
            else:
                dst = ctx.partition_dirs[partition]

            fsops.cp_r(f"{src}/*", f"{dst}/")

    rw_system_add = os.path.join(rom_patches_dir, "rw-system-add.sh")
    if os.path.exists(rw_system_add):
        fsops.append_file(
            rw_system_add, os.path.join(system, "bin/rw-system.sh")
        )

    file_contexts = os.path.join(rom_patches_dir, "file_contexts")
    if os.path.exists(file_contexts):
        fsops.append_file(
            file_contexts,
            os.path.join(system, "etc/selinux/plat_file_contexts"),
        )

    vendor = ctx.partition_dirs.get("vendor")
    if config.get("add_vendor_stuff", False) and vendor:
        mystic_dir = os.path.join(system, "mystic")
        fsops.mkdirp(mystic_dir)
        fsops.cp_r(os.path.join(vendor, "etc/group"), mystic_dir)
        fsops.cp_r(os.path.join(vendor, "etc/passwd"), mystic_dir)
        vendors.run_hook(ctx, "copy_vendor_files")
    if system_ext:
        fsops.drop_lines(
            os.path.join(
                system_ext, "etc/selinux/system_ext_property_contexts"
            ),
            "vendor.camera",
        )

    patches_json = os.path.join(rom_patches_dir, "patches.json")
    if os.path.exists(patches_json):
        ctx.log("Applying framework patches")
        apply_framework_patches(ctx, patches_json, rom_patches_dir)
    vendors.run_hook(ctx, "after_rom_patches")
