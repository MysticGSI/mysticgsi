from ..context import PatchContext


def get_display_name(ctx: PatchContext) -> str:
    system_prop = ctx.partition_prop("system")
    oneui_version = system_prop.get_value("ro.build.version.oneui")
    if oneui_version and len(oneui_version) == 5:
        # OneUI encodes 6.1.1 as 60101, and 6.1 as 60100.
        version = f"{oneui_version[0]}.{oneui_version[2]}"
        if oneui_version[4] != "0":
            version += f".{oneui_version[4]}"
        return f"OneUI {version}"
    return f"OneUI {system_prop.get_android_version()}"
