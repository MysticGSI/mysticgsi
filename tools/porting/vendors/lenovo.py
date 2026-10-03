from ..context import PatchContext


def get_display_name(ctx: PatchContext) -> str:
    version = ctx.partition_prop("system").get_value(
        "ro.external.version.code"
    )
    return f"ZUI [{version}]"
