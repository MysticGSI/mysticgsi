import os

import fsops

from ..binary import hexpatch
from ..context import PatchContext


def get_display_name(ctx: PatchContext) -> str | None:
    version = ctx.partition_prop("system").get_value(
        "ro.build.version.oplusrom"
    )
    if not version:
        return None
    version = version.split("V")[1]
    names = {
        "coloros": "ColorOS",
        "realmeui": "RealmeUI",
        "oxygenos": "OxygenOS",
    }
    return f"{names[ctx.rom_type]} [{version}]"


def patch_binder_monitor(ctx: PatchContext) -> None:
    system_ext = ctx.partition_dirs.get("system_ext")
    if not system_ext:
        ctx.log("Skipping oplus binder monitor patch: no system_ext")
        return

    path = os.path.join(system_ext, "lib64", "liboplusbindermonitor.so")

    # cbnz x20, #0x18; mov w0, #0x40  ->  mov w0, #0x38
    # (purpose of the patch is no longer known)

    hexpatch(ctx, path, "D40000B500088052", "D40000B500078052")


def patch(ctx: PatchContext) -> None:
    system_dir = ctx.partition_dirs["system"]
    my_product = ctx.partition_dirs.get("my_product")
    odm = ctx.partition_dirs.get("odm")
    system = ctx.system_root()

    patch_binder_monitor(ctx)

    if my_product and odm:
        my_product_audio_config = os.path.join(
            my_product, "etc", "audio_policy_configuration.xml"
        )
        odm_audio_config = os.path.join(
            odm, "etc", "virtual_audio_policy_configuration.xml"
        )
        mystic_path = "/system/mystic/virtual_audio_policy_configuration.xml"

        if os.path.exists(odm_audio_config):
            fsops.copy_file(odm_audio_config, os.path.join(system, "mystic"))

            if os.path.exists(my_product_audio_config):
                with open(my_product_audio_config, "r+") as f:
                    data = f.read()
                    data = data.replace(
                        "/odm/etc/virtual_audio_policy_configuration.xml",
                        mystic_path,
                    )

                    f.seek(0)
                    f.write(data)
                    f.truncate()

    phh_files = [
        "etc/fake_audio_policy_volume.xml",
        "etc/usb_audio_policy_configuration.xml",
    ]

    for i in phh_files:
        fsops.rmrf(os.path.join(system, i))

    # Merge my_* build.props so the build info reflects the device.
    for partition in ctx.image_files:
        if not partition.startswith("my_"):
            continue

        partition_dir = ctx.partition_dirs[partition]
        partition_prop_path = f"{partition_dir}/build.prop"
        if not os.path.exists(partition_prop_path):
            partition_prop_path = f"{partition_dir}/etc/build.prop"
            if not os.path.exists(partition_prop_path):
                continue

        remove_props = [
            "ro.product.odm.model",
            "ro.product.first_api_level",
            "ro.product.model",
            "ro.product.name",
            "ro.product.vendor.name",
            "ro.product.device",
            "ro.product.vendor.model",
            "ro.product.odm.name",
            "ro.product.bootimage.model",
            "ro.product.bootimage.name",
            "ro.zygote",
        ]

        for i in remove_props:
            fsops.drop_lines(partition_prop_path, i)

        fsops.append_file(
            partition_prop_path, f"{system_dir}/system/build.prop"
        )

        system_prop = ctx.reload_props(
            "system", f"{system_dir}/system/build.prop"
        )

        ctx.info.device_brand = system_prop.get_device_brand()
        ctx.info.device_manufacturer = system_prop.get_device_manufacturer()
        ctx.info.device_model = (
            system_prop.get_market_name() or system_prop.get_device_model()
        )
        ctx.info.device_codename = system_prop.get_device()
        ctx.info.build_fingerprint = system_prop.get_build_fingerprint()
