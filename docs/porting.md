# Porting patches

`RomPorter.patch()` resolves partitions and captures build metadata, then
calls `tools.porting.pipeline.run(ctx)`. The pipeline keeps shared patches
in their execution order. Patch modules receive a `PatchContext`, not the
build coordinator.

| Module | Responsibility |
| --- | --- |
| `context.py` | Partition lookup, cached properties, version checks, metadata |
| `properties.py` | Parsing and querying Android property files |
| `pipeline.py` | Shared patches and execution order |
| `assets.py` | Patch directories, configuration, overlays, debloat, VNDKs |
| `framework.py` | Decode, edit, rebuild, and replace framework files |
| `binary.py` | Binary replacement helpers and the stock init patch |
| `naming.py` | ROM naming hooks and shared display-name fallback |
| `vendors/` | Vendor edits, naming, and ROM type detection |

Build stages, partition merging, and image generation remain in `make.py`.
`cli.py rebuild` only rebuilds the prepared image tree.

## Adding a vendor patch

Add a module under `tools/porting/vendors/`, import it in that package's
`__init__.py`, and map its ROM type names in `VENDORS`. Related ROM types can
share a module. Types with only patch assets need no module.

```python
import os

import fsops


def get_rom_name(ctx):
    return "ExampleOS"


def patch(ctx):
    fsops.rmrf(os.path.join(ctx.system_root(), "etc/init/example-hal.rc"))
```

Modules implement only the hooks they need:

| Hook | When it runs |
| --- | --- |
| `copy_vendor_files(ctx)` | When `add_vendor_stuff` is enabled and vendor exists |
| `after_rom_patches(ctx)` | After assets and declarative framework patches, if a ROM patch directory exists |
| `patch_frameworks(ctx)` | After shared cleanup, ramdisk, and SELinux edits |
| `patch(ctx)` | After vendor overlays are copied and SELinux mappings removed |
| `get_rom_name(ctx)` | Supplies the ROM prefix used in output image filenames |
| `get_display_name(ctx)` | Supplies the versioned name shown in build summaries |

Naming hooks return a string, or `None` to use the shared fallback. Without
a ROM-name override, the ROM type is capitalized. Display naming tries the
vendor hook, then the custom-ROM properties in `vendors/custom.py`, then
the ROM name and Android version. The variant tag is appended to that last
fallback, preserving the existing naming behavior. Naming-hook modules do
not need to implement patch hooks; Samsung and Lenovo are examples.

AluminiumOS uses the Google module, with additional edits in
`after_rom_patches`. NothingOS retains update engine files through a shared
pipeline condition. Partition placement policies remain in `prepare()`.

Run with `--type exampleos`. `auto` recognizes the signatures listed in
`detect_rom_type()`; adding a module does not add automatic detection.
Keep assets in `patches/<sdk>/<rom_type>/`, or use the codename for preview
builds. Existing JSON, property, overlay, and shell-script formats apply.
Generic assets use the major release directory, such as `patches/all/12/`
for Android `12.1`.

## Context and properties

- `ctx.system_root()` resolves the Android system directory, including
  system-as-root layouts. `ctx.partition_dirs["system"]` is the image root.
- `ctx.partition_prop(name)` reads cached properties. `ctx.part_prop(name)`
  also reads per-model vendor and ODM overrides.
- Properties stay cached during shared patching to preserve stock values.
  When a vendor merges properties that later steps must see, call
  `ctx.reload_props(name)`. Pass a file path as the second argument when the
  merged file differs from the cached path.
- `ctx.info` holds metadata captured before shared property cleanup. Vendor
  patches update its fields when vendor partitions supply better values.
- `ctx.is_android_version(14)` and `ctx.is_android_at_least(14)` use SDK
  levels and preview codenames. Release strings such as `12.1` stay intact.
- Use `ctx.patches_dir` for assets, `fsops` for file operations, and
  `ctx.log(message)` for stage progress or failures.

## Framework edits

Call `edit_framework(path, edit)` from `tools.porting.framework`.
The callback receives a decoded directory. Return `True` after editing it,
or `False` when the patch does not apply. Raise on required edit failures.

The helper rebuilds to a temporary file, rejects missing or empty output,
preserves permissions, and replaces the original only on success.
Declarative patches use the same helper. Decode, patch, and rebuild failures
propagate to the build coordinator.
