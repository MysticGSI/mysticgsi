import os
from collections.abc import Iterable

import fsops

from . import assets, vendors
from .context import PatchContext


def run(ctx: PatchContext) -> None:
    ctx.log(f"Patching Android {ctx.info.android_version} firmware")

    determine_treble_compatibility(ctx)
    detect_64bit_only(ctx)
    clean_build_props(ctx)
    assets.apply_generic_patches(ctx)
    assets.copy_missing_vndks(ctx)
    assets.apply_rom_patches(ctx)
    put_mystic_build_display_id(ctx)

    strip_reboot_on_failure(ctx)
    configure_updatable_apexes(ctx)
    if ctx.is_64bit_only:
        add_64bit_props(ctx)

    nuke_ab_files(ctx)
    remove_unneeded_files(ctx)
    patch_ramdisk(ctx)
    patch_selinux(ctx)
    vendors.run_hook(ctx, "patch_frameworks")
    force_enable_usb_debugging(ctx)

    vendor = ctx.partition_dirs.get("vendor")
    if vendor:
        mystic_dir = os.path.join(ctx.system_root(), "mystic")
        fsops.mkdirp(mystic_dir)
        if ctx.rom_type == "oneui":
            fsops.touch(os.path.join(mystic_dir, "nothing"))

        if os.path.exists(os.path.join(vendor, "overlay")):
            fsops.mkdirp(os.path.join(mystic_dir, "vo"))
            fsops.cp_r(
                os.path.join(vendor, "overlay/*"),
                os.path.join(mystic_dir, "vo"),
            )

    drop_selinux_mappings(ctx)
    vendors.run_hook(ctx, "patch")


AB_FILES = [
    "etc/init/bufferhubd.rc",
    "etc/init/cppreopts.rc",
    "etc/init/otapreopt.rc",
    "etc/init/performanced.rc",
    "etc/init/recovery-persist.rc",
    "etc/init/recovery-refresh.rc",
    "etc/init/update_verifier.rc",
    "etc/init/virtual_touchpad.rc",
    "bin/update_verifier",
]


USB_DEBUGGING_PROPS = [
    ("ro.debuggable=0", "ro.debuggable=1"),
    ("ro.secure=1", "ro.secure=0"),
    ("ro.adb.secure=1", "ro.adb.secure=0"),
]


PAIRED_32BIT_PROGRAMS = ("linker", "linker_asan")


def find_32bit_only_programs(bin_dirs: Iterable[str]) -> list[str]:
    """Programs in bin_dirs that exist only as 32-bit ELF executables."""
    found = set()
    for bin_dir in bin_dirs:
        if not os.path.isdir(bin_dir):
            continue
        for name in os.listdir(bin_dir):
            path = os.path.join(bin_dir, name)
            if (
                name.endswith("32")
                or name in PAIRED_32BIT_PROGRAMS
                or fsops.islink(path)
                or not os.path.isfile(path)
            ):
                continue
            try:
                with open(path, "rb") as f:
                    header = f.read(5)
            except OSError:
                continue
            if header == b"\x7fELF\x01":
                found.add(name)
    return sorted(found)


def remove_unneeded_files(ctx: PatchContext) -> None:
    system = ctx.system_root()
    system_ext = ctx.partition_dirs.get("system_ext")

    useless_files = [
        "verity_key",
        "init.recovery*",
        "recovery-from-boot.*",
    ]

    for file in useless_files:
        fsops.rmrf(os.path.join(ctx.partition_dirs["system"], file))

    # Dolphin can crash surfaceflinger on some ROMs.
    dolphin_lib_path = "lib64/libdolphin.so"

    fsops.rmrf(os.path.join(system, dolphin_lib_path))
    if system_ext:
        fsops.rmrf(os.path.join(system_ext, dolphin_lib_path))

    dirac_apps = ["priv-app/DiracAudioControlService", "app/DiracManager"]

    for app in dirac_apps:
        fsops.rmrf(os.path.join(system, app))

    qcom_location_app = "priv-app/com.qualcomm.location"

    fsops.rmrf(os.path.join(system, qcom_location_app))
    if system_ext:
        fsops.rmrf(os.path.join(system_ext, qcom_location_app))


def patch_ramdisk(ctx: PatchContext) -> None:
    sysdir = ctx.partition_dirs["system"]

    to_remove = ["persist", "bt_firmware", "firmware", "cache"]
    to_symlink = {
        "/vendor/bt_firmware": "bt_firmware",
        "/vendor/firmware": "firmware",
    }

    for i in to_remove:
        fsops.rmrf(os.path.join(sysdir, i))

    for i, value in to_symlink.items():
        fsops.symlink(i, os.path.join(sysdir, value))


def patch_selinux(ctx: PatchContext) -> None:
    clean = [
        "ro.opengles.version",
        "sys.usb.configfs",
        "sys.usb.controller",
        "sys.usb.config",
        "ro.build.fingerprint",
        "software.version",
        "miui.reverse.charge",
        "ro.cust.test",
        "persist.sar.mode",
        "opengles.version",
        "actionable_compatible_property.enabled",
        "vendor.vibrator",
        "vendor.camera",
        "ab_ota_partitions",
        "postinstall.fstab",
        "ro.vendor.trusty.storage.fs_ready",
        "ro.vendor.trusty.storage.fs_ready_rw",
    ]

    system = ctx.partition_dirs["system"]
    system_ext = ctx.partition_dirs.get("system_ext")
    product = ctx.partition_dirs["product"]

    plat_property_contexts = os.path.join(
        system, "etc/selinux/plat_property_contexts"
    )
    plat_file_contexts = os.path.join(system, "etc/selinux/plat_file_contexts")

    for i in clean:
        fsops.drop_lines(plat_property_contexts, i)
        fsops.drop_lines(plat_file_contexts, i)

        if system_ext and os.path.exists(
            os.path.join(system_ext, "etc/selinux")
        ):
            fsops.drop_lines(
                os.path.join(
                    system_ext, "etc/selinux/system_ext_property_contexts"
                ),
                i,
            )

        if os.path.exists(os.path.join(product, "etc/selinux")):
            fsops.drop_lines(
                os.path.join(product, "etc/selinux/product_property_contexts"),
                i,
            )

    if system_ext:
        fsops.drop_lines(
            os.path.join(system_ext, "etc/selinux/system_ext_sepolicy.cil"),
            "genfscon",
        )

    # Enables logcat.
    fsops.sub_lines(
        plat_file_contexts,
        r"u:object_r:logcat_exec:s0",
        r"u:object_r:logd_exec:s0",
    )


def add_64bit_props(ctx: PatchContext) -> None:
    system = ctx.system_root()
    build_prop_path = os.path.join(system, "build.prop")

    fsops.append_text(build_prop_path, "\n# 64-bit only workaround")
    fsops.append_text(build_prop_path, "dalvik.vm.dex2oat64.enabled=true")
    fsops.append_text(build_prop_path, "ro.zygote=zygote64")
    fsops.append_text(build_prop_path, "ro.product.cpu.abilist=arm64-v8a")
    fsops.append_text(build_prop_path, "ro.product.cpu.abilist32=")
    fsops.append_text(build_prop_path, "ro.product.cpu.abilist64=arm64-v8a")


def clean_build_props(ctx: PatchContext) -> None:
    system_prop_path = ctx.partition_prop("system").path
    product_prop_path = ctx.partition_prop("product").path
    system_ext_prop = ctx.partition_prop("system_ext")

    for prop in (
        "ro.build.system_root_image",
        "ro.build.ab_update",
        "media.settings.xml",
        "ro.actionable_compatible_property.enabled",
        "ro.frp.pst",
    ):
        fsops.drop_lines(system_prop_path, prop)

    for prop in (
        "media.settings.xml",
        "ro.product.ab_ota_partitions",
        "ro.sys.sdcardfs",
        "persist.vendor.debug.sensors.accel_cal",
        "persist.vendor.testing_battery_profile",
        "masterclear.allow_retain_esim_profiles_after_fdr",
        "ro.postinstall.fstab.prefix",
        "ro.gfx.driver.1",
        "graphics.gpu.profiler.support",
        "graphics.gpu.profiler.vulkan_layer_apk",
        "ro.vendor.camera.extensions.package",
        "ro.vendor.camera.extensions.service",
        "ro.frp.pst",
    ):
        fsops.drop_lines(product_prop_path, prop)

    if system_ext_prop:
        fsops.drop_lines(system_ext_prop.path, "media.settings.xml")


def detect_64bit_only(ctx: PatchContext) -> None:
    system = ctx.system_root()

    if not os.path.exists(os.path.join(system, "lib", "libandroid.so")):
        ctx.log("This ROM is 64-bit only")
        ctx.is_64bit_only = True
        return

    bin_dirs = [os.path.join(system, "bin")] + [
        os.path.join(ctx.partition_dirs[p], "bin")
        for p in ("system_ext", "product")
        if p in ctx.partition_dirs
    ]
    ctx.programs_32bit_only = find_32bit_only_programs(bin_dirs)
    if ctx.programs_32bit_only:
        ctx.log(
            "Warning: this image needs a device with 32-bit "
            "support; these programs are 32-bit only: "
            f"{', '.join(ctx.programs_32bit_only)}"
        )


def put_mystic_build_display_id(ctx: PatchContext) -> None:
    system_prop = ctx.partition_prop("system")
    product_prop = ctx.partition_prop("product")

    props = [
        "ro.build.display.id",
        "ro.product.build.id",
        "ro.system.build.id",
        "ro.build.id",
    ]

    for prop in (system_prop, product_prop):
        # A product build.prop may be empty when the ROM ships none.
        keys = prop.exists_any(props)
        if keys:
            fsops.set_props(prop.path, keys[0], "Ported.Using.MysticGSI.Tool")


def nuke_ab_files(ctx: PatchContext) -> None:
    system = ctx.system_root()

    ab_files = list(AB_FILES)

    # NothingOS's StorageManagerService depends on update_engine.
    if ctx.rom_type != "nothing":
        ab_files.append("etc/init/update_engine.rc")
        ab_files.append("bin/update_engine")

    for i in ab_files:
        fsops.rmrf(os.path.join(system, i))


def configure_updatable_apexes(ctx: PatchContext) -> None:
    system = ctx.system_root()
    system_prop_path = ctx.partition_prop("system").path

    apex_updatable = any(
        file.endswith(".apex")
        for _, _, files in os.walk(os.path.join(system, "apex"))
        for file in files
    )

    if apex_updatable:
        fsops.append_text(system_prop_path, "\nro.apex.updatable=true")


def strip_reboot_on_failure(ctx: PatchContext) -> None:
    system = ctx.system_root()

    paths = [
        os.path.join(system, "etc/init/hw/init.rc"),
        os.path.join(system, "etc/init/apexd.rc"),
    ]

    for path in paths:
        fsops.drop_lines(path, "reboot_on_failure")


def drop_selinux_mappings(ctx: PatchContext) -> None:
    product = ctx.partition_dirs["product"]
    system_ext = ctx.partition_dirs.get("system_ext")
    selinux_mapping_path = "etc/selinux/mapping/*"

    fsops.rmrf(os.path.join(product, selinux_mapping_path))
    if system_ext:
        fsops.rmrf(os.path.join(system_ext, selinux_mapping_path))


def force_enable_usb_debugging(ctx: PatchContext) -> None:
    system = ctx.system_root()
    prop_files = [ctx.partition_prop("system").path]
    prop_default = os.path.join(system, "etc/prop.default")
    if os.path.exists(prop_default):
        prop_files.append(prop_default)

    for path in prop_files:
        for pattern, repl in USB_DEBUGGING_PROPS:
            fsops.sub_lines(path, pattern, repl)


def determine_treble_compatibility(ctx: PatchContext) -> None:
    system = ctx.system_root()
    system_prop = ctx.partition_prop("system")
    if not system_prop.is_true("ro.treble.enabled"):
        ctx.log("This firmware isn't Treble supported but fine.")
        if os.path.exists(os.path.join(system, "vendor")):
            fsops.rmrf(os.path.join(system, "vendor", "*"))
