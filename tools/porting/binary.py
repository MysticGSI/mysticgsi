import os


def hexpatch(ctx, filepath: str, original: str, patched: str):
    if not os.path.exists(filepath):
        ctx.log(f"hexpatch: {filepath} not found")
        return

    with open(filepath, "rb+") as file:
        data = file.read().hex().upper()
        data = data.replace(original.upper(), patched.upper())

        file.seek(0)
        file.write(bytes.fromhex(data))
        file.truncate()


def patch_init(ctx):
    system = ctx.system_root()

    # security_setenforce(1) -> security_setenforce(0): permissive SELinux
    hexpatch(
        ctx,
        os.path.join(system, "bin", "init"),
        "1F0400710001005420008052",
        "1F0400710001005400008052",
    )
