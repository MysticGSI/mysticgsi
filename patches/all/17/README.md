# Android 17 Treble PHH and APEX compatibility

The shared Android 17 patch set already provides Treble PHH boot/data hooks and APEX/VNDK compatibility handling. Device profiles should reuse these shared assets rather than copying them into OEM-specific patch folders.

For Moto G15 (`lamu`) on LineageOS 24.0, kernel 6.6 images and module lists are device-build artifacts, not extracted system/vendor ROM patches. See `patches/37/lamu/README.md` for the upstream LineageOS source mapping.
