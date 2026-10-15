"""
LG vendor module for MysticGSI.

LG stock ROMs are identified by ro.product.brand == "lge", which is
universal across every LG device. There is NO ro.lge.ui.version prop —
the LG UX version lives in the vendor partition as
ro.vendor.lge.lguiversion (e.g. "9.2").
"""

from ..context import PatchContext


def get_display_name(ctx: PatchContext) -> str:
    """Return a human-readable name for LG stock ROMs.

    Priority:
      1. ro.vendor.lge.lguiversion      — LG UX version (9.2, 10.0, ...)
      2. ro.vendor.lge.swversion_short  — short sw version (V20a)
      3. ro.vendor.lge.swversion        — full sw version (X430BMW20a)
      4. Android version                — absolute fallback
    """
    vendor_prop = ctx.partition_prop("vendor")
    system_prop = ctx.partition_prop("system")
    android_version = system_prop.get_android_version()

    lge_ui_version = vendor_prop.get_value("ro.vendor.lge.lguiversion")
    if lge_ui_version:
        return f"LG UX {lge_ui_version} (Android {android_version})"

    sw_short = vendor_prop.get_value("ro.vendor.lge.swversion_short")
    if sw_short:
        return f"LG UX {sw_short} (Android {android_version})"

    sw_version = vendor_prop.get_value("ro.vendor.lge.swversion")
    if sw_version:
        return f"LG UX {sw_version} (Android {android_version})"

    return f"LG UX (Android {android_version})"
