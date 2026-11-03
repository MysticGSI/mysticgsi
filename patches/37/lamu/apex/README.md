# Lamu APEX notes

LineageOS 24.0 uses the standard Android APEX framework together with the MT6768 common device configuration. The Moto G15 firmware dump includes vendor APEX payloads, but these are proprietary binaries and are not patch scripts.

Treble PHH boot/data hooks and VNDK compatibility are shared Android 17 behavior; keep them in `patches/all/17` and `patches/vndk/37`, not in a device-specific Lamu patch. Do not replace or bind-mount vendor APEX payloads without a verified need and a compatible license/source.

If a Lamu-specific APEX compatibility issue is confirmed, document the affected package, vendor/build fingerprint, and tested fix before adding an asset here.
