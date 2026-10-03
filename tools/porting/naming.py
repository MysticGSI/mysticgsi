from . import vendors
from .vendors import custom


def get_rom_name(ctx):
    return vendors.run_hook(ctx, "get_rom_name") or ctx.rom_type.capitalize()


def get_display_name(ctx, variant_tag=""):
    android_version = str(ctx.partition_prop("system").get_android_version())
    try:
        name = vendors.run_hook(ctx, "get_display_name")
        if name:
            return name
        name = custom.get_display_name(ctx)
        if name:
            return name
    except Exception as e:
        ctx.log(f"Failed to determine the ROM display name: {e}")

    result = get_rom_name(ctx)
    if not android_version.isdigit():
        result += f" Android {android_version}"
    else:
        result += f" {float(android_version)}"
    if variant_tag:
        result += f" {variant_tag.replace('-', ' ')}"
    return result
