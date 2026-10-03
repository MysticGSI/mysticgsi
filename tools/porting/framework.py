import json
import os
import tempfile

import fsops


APKTOOL_JAR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "apktool", "apktool.jar"
)


def apktool(*args, cwd=None):
    return fsops.run(["java", "-jar", APKTOOL_JAR, *args], cwd=cwd)


def edit_framework(path, edit, *, no_debug_info=False):
    path = os.path.abspath(path)
    framework = os.path.basename(path)
    with tempfile.TemporaryDirectory(
        prefix="framework-", dir=os.path.dirname(path)
    ) as staging:
        out_dir = os.path.join(staging, "decoded")
        options = ["--no-debug-info"] if no_debug_info else []
        if apktool("d", "-f", *options, "-o", out_dir, path) != 0:
            raise RuntimeError(f"apktool failed to decode {framework}")
        if not edit(out_dir):
            return

        rebuilt = os.path.join(staging, framework)
        if apktool("b", "-o", rebuilt, cwd=out_dir) != 0:
            raise RuntimeError(f"apktool failed to rebuild {framework}")
        if not os.path.isfile(rebuilt) or os.path.getsize(rebuilt) == 0:
            raise RuntimeError(f"apktool produced no {framework}")
        os.chmod(rebuilt, os.stat(path).st_mode & 0o7777)
        os.replace(rebuilt, path)


def apply_framework_patches(ctx, patches_json, rom_patches_dir):
    with open(patches_json, "r") as f:
        patches_data = json.load(f)
    for partition, frameworks in patches_data.items():
        for framework, patch_names in frameworks.items():
            path = os.path.join(ctx.partition_dirs[partition], framework)
            if os.path.exists(path):
                patch_framework(path, patch_names, rom_patches_dir)


def patch_framework(path, patch_names, rom_patches_dir):
    def edit(out_dir):
        for patch_name in patch_names:
            patch_file = os.path.join(
                rom_patches_dir, "framework-patches", f"{patch_name}.patch"
            )
            rc = fsops.run(
                ["patch", "-p0", "-s", "-t", "-N", "--no-backup-if-mismatch"],
                cwd=out_dir,
                stdin=patch_file,
            )
            if rc != 0:
                raise RuntimeError(
                    f"Failed to apply {patch_name} to {path} ({rc})"
                )
        return True

    # apktool 3 dropped the short -b form of --no-debug-info.
    edit_framework(path, edit, no_debug_info=True)
