mount -o bind /mnt/phh/empty_dir /vendor/app || true
if [ "$vndk" == 28 ]; then
  mount -o bind /system/bin/wpa_supplicant /vendor/bin/hw/wpa_supplicant || true
fi

# Mata o HAL de USB da LG para o Android controlar o USB
setprop ctl.stop vendor.lge-usb-hal-1-0 2>/dev/null || true
setprop ctl.stop vendor.lge-usb-sh 2>/dev/null || true
