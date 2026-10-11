from ..context import PatchContext


def get_display_name(ctx: PatchContext) -> str:
    system_prop = ctx.partition_prop("system")
    lge_ui_version = system_prop.get_value("ro.lge.ui.version")
    android_version = system_prop.get_android_version()
    
    if lge_ui_version:
        return f"LG UX {lge_ui_version} (Android {android_version})"
    return f"LG UX (Android {android_version})"
