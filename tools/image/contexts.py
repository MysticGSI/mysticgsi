"""
SELinux file_contexts generation for the system image.
"""

from typing import List, Optional
import os

EXTRA_FILE_CONTEXTS: List[str] = [
    r"/firmware(/.*)?         u:object_r:firmware_file:s0",
    r"/firmware-modem(/.*)?   u:object_r:firmware_file:s0",
    r"/bt_firmware(/.*)?      u:object_r:bt_firmware_file:s0",
    r"/persist(/.*)?          u:object_r:mnt_vendor_file:s0",
    r"/sepolicy(/.*)?         u:object_r:sepolicy_file:s0",
    r"/dsp                    u:object_r:rootfs:s0",
    r"/oem                    u:object_r:rootfs:s0",
    r"/op1                    u:object_r:rootfs:s0",
    r"/op2                    u:object_r:rootfs:s0",
    r"/charger_log            u:object_r:rootfs:s0",
    r"/audit_filter_table     u:object_r:rootfs:s0",
    r"/keydata                u:object_r:rootfs:s0",
    r"/keyrefuge              u:object_r:rootfs:s0",
    r"/omr                    u:object_r:rootfs:s0",
    r"/publiccert.pem         u:object_r:rootfs:s0",
    r"/sepolicy_version       u:object_r:rootfs:s0",
    r"/cust                   u:object_r:rootfs:s0",
    r"/donuts_key             u:object_r:rootfs:s0",
    r"/v_key                  u:object_r:rootfs:s0",
    r"/carrier                u:object_r:rootfs:s0",
    r"/dqmdbg                 u:object_r:rootfs:s0",
    r"/ADF                    u:object_r:rootfs:s0",
    r"/APD                    u:object_r:rootfs:s0",
    r"/asdf                   u:object_r:rootfs:s0",
    r"/batinfo                u:object_r:rootfs:s0",
    r"/voucher                u:object_r:rootfs:s0",
    r"/xrom                   u:object_r:rootfs:s0",
    r"/custom                 u:object_r:rootfs:s0",
    r"/cpefs                  u:object_r:rootfs:s0",
    r"/modem                  u:object_r:rootfs:s0",
    r"/module_hashes          u:object_r:rootfs:s0",
    r"/pds                    u:object_r:rootfs:s0",
    r"/tombstones             u:object_r:rootfs:s0",
    r"/factory                u:object_r:rootfs:s0",
    r"/logdata                u:object_r:rootfs:s0",
    r"/oneplus(/.*)?          u:object_r:rootfs:s0",
    r"/addon.d                u:object_r:rootfs:s0",
    r"/op_odm                 u:object_r:rootfs:s0",
    r"/avb                    u:object_r:rootfs:s0",
    r"/3rdmodem               u:object_r:rootfs:s0",
    r"/3rdmodemnvm            u:object_r:rootfs:s0",
    r"/3rdmodemnvmbkp         u:object_r:rootfs:s0",
    r"/modem_log              u:object_r:rootfs:s0",
    r"/patch_hw               u:object_r:rootfs:s0",
    r"/preas                  u:object_r:rootfs:s0",
    r"/preload                u:object_r:rootfs:s0",
    r"/splash2                u:object_r:rootfs:s0",
    r"/prets                  u:object_r:rootfs:s0",
    r"/preavs                 u:object_r:rootfs:s0",
    r"/pretvs                 u:object_r:rootfs:s0",
    r"/eng                    u:object_r:rootfs:s0",
    r"/log                    u:object_r:rootfs:s0",
    r"/hw_product             u:object_r:rootfs:s0",
    r"/version                u:object_r:rootfs:s0",
    r"/sec_storage            u:object_r:rootfs:s0",
    r"/mi_ext                 u:object_r:rootfs:s0",
    r"/opcust                 u:object_r:rootfs:s0",
    r"/opconfig               u:object_r:rootfs:s0",
    r"/preavs(/.*)?           u:object_r:system_file:s0",
    r"/preas(/.*)?            u:object_r:system_file:s0",
    r"/mi_ext(/.*)?           u:object_r:system_file:s0",
    r"/cust(/.*)?             u:object_r:system_file:s0",
    r"/hw_product(/.*)?       u:object_r:system_file:s0",
    r"/product_h(/.*)?        u:object_r:system_file:s0",
    r"/my_bigball(/.*)?       u:object_r:system_file:s0",
    r"/my_company             u:object_r:system_file:s0",
    r"/my_custom              u:object_r:system_file:s0",
    r"/my_engineering(/.*)?   u:object_r:system_file:s0",
    r"/my_heytap(/.*)?        u:object_r:system_file:s0",
    r"/my_manifest(/.*)?      u:object_r:system_file:s0",
    r"/my_product(/.*)?       u:object_r:system_file:s0",
    r"/my_region(/.*)?        u:object_r:system_file:s0",
    r"/my_stock(/.*)?         u:object_r:system_file:s0",
    r"/my_carrier(/.*)?       u:object_r:system_file:s0",
    r"/my_preload             u:object_r:system_file:s0",
    r"/my_reserve             u:object_r:system_file:s0",
    r"/my_carrier             u:object_r:system_file:s0",
    r"/my_version             u:object_r:system_file:s0",
    r"/special_preload        u:object_r:system_file:s0",
    r"/resetFactory.cfg       u:object_r:rootfs:s0",
    r"/vgc                    u:object_r:rootfs:s0",
    r"/tranfs                 u:object_r:rootfs:s0",
    r"/logdump                u:object_r:rootfs:s0",
    r"/patch_hn               u:object_r:rootfs:s0",
    r"/product_h              u:object_r:rootfs:s0",
    r"/logbuf                 u:object_r:rootfs:s0",
    r"/eri                    u:object_r:rootfs:s0",
    r"/persdata               u:object_r:rootfs:s0",
    r"/vzw                    u:object_r:rootfs:s0",
    r"/blackbox               u:object_r:rootfs:s0",
    r"/elabel                 u:object_r:rootfs:s0",
    r"/vision                 u:object_r:rootfs:s0",
    r"/oempersist             u:object_r:rootfs:s0",
    r"/wt_custom              u:object_r:rootfs:s0",
    r"/fsg                    u:object_r:rootfs:s0",
    r"/dpolicy                u:object_r:rootfs:s0",
    r"/spu                    u:object_r:rootfs:s0",
    r"/ai_model               u:object_r:rootfs:s0",
    r"/tr_carrier(/.*)?       u:object_r:system_file:s0",
    r"/tr_company(/.*)?       u:object_r:system_file:s0",
    r"/tr_mi(/.*)?            u:object_r:system_file:s0",
    r"/tr_preload(/.*)?       u:object_r:system_file:s0",
    r"/tr_product(/.*)?       u:object_r:system_file:s0",
    r"/tr_region(/.*)?        u:object_r:system_file:s0",
    r"/tr_theme(/.*)?         u:object_r:system_file:s0",
    r"/ai_model_vendor        u:object_r:rootfs:s0",
    r"/dji_apk                u:object_r:rootfs:s0",
    r"/soccp_firmware         u:object_r:rootfs:s0",
]


SELINUX_DIRS = (
    "system/etc/selinux",
    "system/vendor/etc/selinux",
    "system_ext/etc/selinux",
    "system/system_ext/etc/selinux",
)
CONTEXT_FILES = (
    "plat_file_contexts",
    "nonplat_file_contexts",
    "system_ext_file_contexts",
    "product_file_contexts",
)


def _read_contexts(path: str) -> List[str]:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [line.strip() for line in f]
    except OSError:
        return []
    return [line for line in lines if line and not line.startswith('#')]


def prepare_file_contexts(system_dir: str, output_file: str) -> Optional[str]:
    """
    Writes the ROM's own file_contexts plus EXTRA_FILE_CONTEXTS to
    output_file. Returns None if the ROM has no file_contexts at all.
    """
    contexts: List[str] = []
    for s_dir in SELINUX_DIRS:
        for c_name in CONTEXT_FILES:
            contexts += _read_contexts(os.path.join(system_dir, s_dir, c_name))

    if not contexts:
        return None
    contexts += EXTRA_FILE_CONTEXTS

    out_dir = os.path.dirname(output_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(f"{ctx}\n" for ctx in contexts)

    return output_file
