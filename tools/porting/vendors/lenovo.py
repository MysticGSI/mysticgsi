def get_display_name(ctx):
    version = ctx.partition_prop("system").get_value(
        "ro.external.version.code"
    )
    return f"ZUI [{version}]"
