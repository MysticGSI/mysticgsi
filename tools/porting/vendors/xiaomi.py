import os

import fsops

from ..context import PatchContext
from ..framework import edit_framework


def get_display_name(ctx: PatchContext) -> str | None:
    system_prop = ctx.partition_prop("system")
    if ctx.rom_type == "miui":
        return f"MIUI [{system_prop.get_build_incremental()}]"
    if ctx.rom_type == "hyperos":
        return f"HyperOS [{system_prop.get_hyperos_version()}]"
    return None


def patch(ctx: PatchContext) -> None:
    system_dir = ctx.partition_dirs["system"]
    mi_ext = ctx.partition_dirs.get("mi_ext")
    product = ctx.partition_dirs["product"]
    system = os.path.join(system_dir, "system")

    device_features_path = os.path.join(product, "etc/device_features")
    if ctx.is_android_version(11):
        device_features_path = (
            f"{ctx.partition_dirs['system']}/mystic/device_features"
        )
    elif not ctx.is_android_at_least(10):
        device_features_path = f"{system}/etc/device_features"

    fsops.cp_r(
        os.path.join(ctx.patches_dir, "miui_device_features", "*"),
        device_features_path,
        clobber=False,
    )

    # mi_ext carries the HyperOS logo and version.
    if mi_ext:
        fsops.cp_r(f"{mi_ext}/product/*", product)
        fsops.cp_r(f"{mi_ext}/system/*", system)
        fsops.rmrf(f"{mi_ext}/system/*")
        fsops.rmrf(f"{mi_ext}/product/*")
        fsops.append_file(f"{mi_ext}/etc/build.prop", f"{system}/build.prop")

        # Reload so the mi_ext props appended above are visible later.
        ctx.reload_props("system", os.path.join(system, "build.prop"))

    if product:
        poco_launcher_path = os.path.join(
            product, "priv-app", "MiLauncherGlobal"
        )

        if os.path.exists(poco_launcher_path):
            fsops.append_text(
                os.path.join(system, "build.prop"),
                "\nro.miui.product.home=com.mi.android.globallauncher",
            )


def patch_frameworks(ctx: PatchContext) -> None:
    if ctx.rom_type not in ("miui", "hyperos"):
        return
    if not ctx.is_android_version(14):
        return
    path = os.path.join(
        ctx.partition_dirs["system_ext"], "framework", "miui-services.jar"
    )
    edit_framework(path, _edit_services)


def _edit_services(out_dir: str) -> bool:
    # Fix brightness in Xiaomi 14 ports (HyperOS 1.0 only).
    hysteric_path = os.path.join(
        out_dir,
        "smali/com/android/server/display/HysteresisLevelsImpl.smali",
    )
    if not os.path.exists(hysteric_path):
        return False
    with open(hysteric_path, "r") as f:
        content = f.read()
    updated = content.replace(
        "iget v1, v1, Lcom/android/server/display/"
        "DisplayDeviceConfig$HighBrightnessModeData;->minimumLux:F",
        "const/high16 v1, 0x3f800000    # 1.0f",
    )
    if updated == content:
        return False
    with open(hysteric_path, "w") as f:
        f.write(updated)
    return True
