# Motorola Moto G15 (lamu), LineageOS 24.0

This Android 17 patch profile follows the public LineageOS 24.0 Lamu device configuration. It keeps the stock init and avoids installing unrelated GSI device overlays.

## Source of truth

- Device tree: `LineageOS/android_device_motorola_lamu`, branch `lineage-24.0`
- Common MT6768 tree: `LineageOS/android_device_motorola_mt6768-common`, branch `lineage-24.0`
- Kernel/module prebuilts: `LineageOS/android_device_motorola_lamu-kernels`, branch `lineage-24.0`, directory `6.6/`

The device tree selects the `mgk_64_k66` kernel device and consumes the kernel image, DTBO, and module-load lists from the 6.6 prebuilt directory. MysticGSI's ROM patch stage patches extracted system/vendor trees; it does not flash boot, vendor_boot, DTBO, or kernel-module images. Keep those binaries and module lists in the device/kernel build source rather than copying them into ROM patch assets.

Shared Treble PHH and VNDK/APEX handling remains in `patches/all/17` and `patches/vndk/37`. Do not duplicate generic fixes here. Add a Lamu-only patch under this directory only when verified against LineageOS 24.0 and the Lamu vendor build.
