import os

import fsops

from ..context import PatchContext
from ..framework import edit_framework


def get_display_name(ctx: PatchContext) -> str:
    version = ctx.partition_prop("system").get_value("ro.nothing.version.id")
    return f"NothingOS [{version}]"


def copy_vendor_files(ctx: PatchContext) -> None:
    fsops.cp_r(
        os.path.join(
            ctx.partition_dirs["vendor"],
            "etc/display_refresh_rate_config.json",
        ),
        os.path.join(ctx.system_root(), "mystic"),
    )


def patch_frameworks(ctx: PatchContext) -> None:
    if not ctx.is_android_version(13):
        return
    path = os.path.join(ctx.system_root(), "framework", "services.jar")
    edit_framework(path, _edit_services)


def _edit_services(out_dir: str) -> bool:
    patched = False
    # ChargeLevelUpdater depends on Nothing's HALs and can bootloop.
    charge_updater = os.path.join(
        out_dir,
        "smali_classes2/com/nothing/server/"
        "BatteryChargeManager$ChargeLevelUpdater.smali",
    )
    if os.path.exists(charge_updater):
        with open(charge_updater, "r") as f:
            lines = f.read().split("\n")
        try:
            start = lines.index(".method public run()V")
            end = lines.index(".end method", start + 1)
            locals_index = next(
                i for i in range(start + 1, end) if ".locals" in lines[i]
            )
            return_index = next(
                i
                for i in range(locals_index + 1, end)
                if "return-void" in lines[i]
            )
        except (ValueError, StopIteration) as e:
            raise RuntimeError(
                "Cannot patch BatteryChargeManager: unexpected run() method"
            ) from e
        lines[locals_index + 1 : return_index] = []
        with open(charge_updater, "w") as f:
            f.write("\n".join(lines))
        patched = True

    refresh_parser = os.path.join(
        out_dir,
        "smali_classes2/com/android/server/wm/"
        "NtRefreshRateController$NtRefreshRateFileParser.smali",
    )
    if os.path.exists(refresh_parser):
        with open(refresh_parser, "r") as f:
            content = f.read()
        updated = content.replace(
            "/vendor/etc/display_refresh_rate_config.json",
            "/system/mystic/display_refresh_rate_config.json",
        )
        if updated != content:
            with open(refresh_parser, "w") as f:
                f.write(updated)
            patched = True
    return patched
