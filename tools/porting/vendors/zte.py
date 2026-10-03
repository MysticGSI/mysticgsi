import os

import fsops

from ..context import PatchContext


def get_display_name(ctx: PatchContext) -> str | None:
    names = {"myos": "MyOS", "nebulaos": "NebulaOS"}
    name = names.get(ctx.rom_type)
    if name is None:
        return None
    display_id = ctx.partition_prop("system").get_display_build_id()
    version = display_id.split("_")[0].split(name)[1]
    return f"{name} [{version}]"


def patch(ctx: PatchContext) -> None:
    vendor_prop = ctx.part_prop("vendor")
    if not vendor_prop:
        return

    system_path = ctx.system_root()

    features_to_skip = [
        "ro.vendor.feature.zte_feature_awinic_vib",
        "ro.vendor.feature.zte_feature_zperf_cube",
        "ro.vendor.feature.zte_feature_cube_thermallevel_control",
    ]

    vendor_features = vendor_prop.starts_with("ro.vendor.feature")
    for key, value in vendor_features.items():
        if key not in features_to_skip:
            fsops.append_text(
                os.path.join(system_path, "build.prop"), f"\n{key}={value}"
            )
