# Lamu kernel 6.6 notes

LineageOS 24.0's Lamu tree selects `mgk_64_k66` and uses artifacts from `LineageOS/android_device_motorola_lamu-kernels:lineage-24.0/6.6/`.

The published source separates kernel deliverables from ROM partition patches: kernel image, DTB/DTBO, UAPI headers, and `system_dlkm`/`vendor_dlkm`/vendor-ramdisk module lists. MysticGSI's port pipeline cannot apply or flash these artifacts. Do not add a dummy kernel patch or embed the prebuilt images here; consume them through the device/kernel integration when building or flashing a Lamu image.

Only add text patch assets here when an identified kernel-6.6/vendor incompatibility has a reproducible fix that belongs in the extracted ROM tree.
