# LineageOS 24.0 (Android 17)

LineageOS 24.0 is handled as Android 17. Shared Android 17 compatibility patches remain in `patches/all/17` and are applied by the common patch pipeline.

This ROM-specific configuration preserves LineageOS's stock init and device overlays rather than replacing them with generic GSI assets. Add a LineageOS-only patch here only when it is required across LineageOS devices; keep Moto G15-only APEX or kernel adjustments in the device-specific patch area.
