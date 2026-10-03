import os

from ..properties import SettingsProp
from . import google, huawei, lenovo, nothing, oplus, samsung, xiaomi, zte


VENDORS = {
    "pixel": google,
    "alos": google,
    "emui": huawei,
    "magicos": huawei,
    "harmonyos": huawei,
    "zui": lenovo,
    "nothing": nothing,
    "realmeui": oplus,
    "oxygenos": oplus,
    "coloros": oplus,
    "oneui": samsung,
    "miui": xiaomi,
    "hyperos": xiaomi,
    "joyui": xiaomi,
    "myos": zte,
    "redmagic": zte,
    "nebulaos": zte,
}


def run_hook(ctx, name):
    vendor = VENDORS.get(ctx.rom_type)
    hook = getattr(vendor, name, None)
    if hook is not None:
        return hook(ctx)
    return None


def detect_rom_type(ctx):
    if ctx.rom_type not in ("generic", "custom", "auto"):
        return

    if ctx.override_rom_type != "default":
        ctx.rom_type = ctx.override_rom_type
        return

    custom_rom_props = {
        "lineageOS": ["ro.lineage.build.version"],
        "evolutionx": ["org.evolution.build_version"],
        "crDroid": ["ro.crdroid.version"],
        "pixelexperience": ["org.pixelexperience.version"],
        "projectblaze": ["org.blaze.version"],
        "risingos": ["ro.rising.version"],
        "voltageos": ["org.voltage.version"],
    }

    system_prop = ctx.partition_prop("system")
    product_prop = ctx.partition_prop("product")
    props = (system_prop, product_prop)

    for rom, value in custom_rom_props.items():
        for prop in props:
            if not prop:
                continue
            for key in value:
                if prop.exists(key):
                    ctx.rom_type = rom
                    return

    if ctx.rom_type != "auto":
        return

    vendor_rom_props = {
        "oneui": "ro.build.version.oneui",
        "hyperos": "ro.mi.os.version.incremental",
        "miui": "ro.miui.ui.version.name",
        "nothing": "ro.nothing.version.id",
    }
    for rom, key in vendor_rom_props.items():
        if any(prop and prop.get_value(key) for prop in props):
            ctx.rom_type = rom
            return

    brands = {
        prop.get_device_brand().casefold()
        for prop in props
        if prop and prop.get_device_brand()
    }
    if any(
        prop and prop.get_value("ro.build.version.oplusrom") for prop in props
    ):
        oplus_types = {
            "oppo": "coloros",
            "realme": "realmeui",
            "oneplus": "oxygenos",
        }
        for brand in ("oppo", "realme", "oneplus"):
            if brand in brands:
                ctx.rom_type = oplus_types[brand]
                return

    honor_brand = "honor" in brands
    for partition in ("product_h", "hw_product"):
        part_dir = ctx.partition_dirs.get(partition)
        if not part_dir:
            continue
        local_prop_path = os.path.join(part_dir, "etc", "prop", "local.prop")
        if not os.path.isfile(local_prop_path):
            continue
        local_prop = SettingsProp()
        local_prop.init_from_file(local_prop_path)
        if partition == "product_h":
            honor_brand = honor_brand or (
                (local_prop.get_device_brand() or "").casefold() == "honor"
            )
        version = (
            local_prop.get_value("ro.comp.hl.product_base_version") or ""
        ).casefold()
        for rom in ("magicos", "harmonyos", "emui"):
            if version.startswith(rom):
                ctx.rom_type = rom
                return

    if ctx.partition_dirs.get("product_h") and honor_brand:
        ctx.rom_type = "magicos"
