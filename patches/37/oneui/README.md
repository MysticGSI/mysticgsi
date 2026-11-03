# One UI ROM compatibility for Android 17 / LineageOS 24.0 base

This port keeps stock init, disables generic device overlays, and enables the existing vendor-file handling. Shared compatibility fixes stay in `patches/all/17`.

Do not add framework bytecode patches until they are verified against the target One UI Android 17 framework; the Android 16 KnoxGuard patch is intentionally not carried forward without that validation.
