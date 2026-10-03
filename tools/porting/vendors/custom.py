from ..context import PatchContext
from ..properties import SettingsProp


def get_display_name(ctx: PatchContext) -> str | None:
    system_prop = ctx.partition_prop("system")
    product_prop = ctx.partition_prop("product") or SettingsProp()

    if product_prop.exists("org.evolution.build_version"):
        version = product_prop.get_value("org.evolution.build_version")
        return f"Evolution X {version}"
    if system_prop.exists("ro.crdroid.version"):
        return f"crDroid {system_prop.get_value('ro.modversion')}"
    if system_prop.exists("org.pixelexperience.version"):
        build_type = system_prop.get_value("org.pixelexperience.build_type")
        android_version = system_prop.get_android_version()
        return f"PixelExperience {build_type} {android_version}.0"
    if system_prop.exists("org.blaze.version"):
        version = system_prop.get_value("org.blaze.version")
        build_type = system_prop.get_value("ro.blaze.buildtype")
        return f"Project Blaze {version} ({build_type})"
    if product_prop.exists("ro.rising.version") or system_prop.exists(
        "ro.rising.version"
    ):

        def rising(key: str) -> str | None:
            return product_prop.get_value(key) or system_prop.get_value(key)

        return (
            f"RisingOS [{rising('ro.rising.version')}] "
            f"({rising('ro.rising.releasetype')}, "
            f"{rising('ro.rising.packagetype')})"
        )
    if system_prop.exists("org.voltage.version"):
        version = system_prop.get_value("org.voltage.version")
        return f"VoltageOS [{version}]"
    if system_prop.exists("ro.lineage.build.version"):
        version = system_prop.get_value("ro.lineage.build.version")
        return f"LineageOS {version}"
    return None
