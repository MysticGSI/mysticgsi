"""
SELinux file_contexts generation for the system image.
"""

from typing import Callable, Dict, List, Optional
import os
import re

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
    "etc/selinux",
    "system/etc/selinux",
    "system/vendor/etc/selinux",
    "product/etc/selinux",
    "system/product/etc/selinux",
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
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f]
    except OSError:
        return []
    return [line for line in lines if line and not line.startswith("#")]


def _covered_by(contexts: List[str]) -> Callable[[str], bool]:
    """Whether some file_contexts rule matches a path, ignoring file type
    restrictions. Rules Python's re can't parse are left out."""
    patterns = []
    for line in contexts:
        spec = line.split(None, 1)[0]
        try:
            re.compile(spec)
        except re.error:
            continue
        patterns.append(f"(?:{spec})")
    combined = re.compile("|".join(patterns)) if patterns else None
    return lambda path: bool(combined and combined.fullmatch(path))


_LITERAL_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/_-"
)


def _literal_spec(path: str) -> str:
    """
    A file_contexts regex matching exactly path. libselinux rejects
    non-ASCII in the file, and spaces would split the line, so those bytes
    become \\xHH; other punctuation is backslash-escaped. Escaped characters
    don't count as regex metacharacters, so libselinux treats the rule as
    an exact path, which takes precedence over pattern rules.
    """
    out = []
    for byte in os.fsencode(path):
        if byte in _LITERAL_BYTES:
            out.append(chr(byte))
        elif 0x20 < byte < 0x7F:
            out.append("\\" + chr(byte))
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out)


def _stock_label_gaps(
    system_dir: str,
    contexts: List[str],
    stock_labels: Dict[str, str],
    mount_point: str = "/",
) -> List[str]:
    """
    Exact rules giving the paths in system_dir that no ROM rule covers the
    label their partition image had. Paths the ROM's rules (and the patches
    applied to them) do cover keep getting labelled by those rules.
    """
    covered = _covered_by(contexts)
    gaps = []
    for path, label in sorted(stock_labels.items()):
        mounted_path = path
        if mount_point != "/":
            mounted_path = mount_point.rstrip("/")
            if path != "/":
                mounted_path += path
        if (
            label
            and not any(c.isspace() for c in label)
            and os.path.lexists(system_dir + path)
            and not covered(mounted_path)
        ):
            gaps.append(f"{_literal_spec(mounted_path)} {label}")
    return gaps


def prepare_file_contexts(
    system_dir: str,
    output_file: str,
    stock_labels: Optional[Dict[str, str]] = None,
    context_dirs: Optional[List[str]] = None,
    mount_point: str = "/",
) -> Optional[str]:
    """
    Writes the ROM's own file_contexts, exact rules for the paths they miss
    from stock_labels (image path -> label), and EXTRA_FILE_CONTEXTS as a
    last resort to output_file. Returns None if the ROM has no
    file_contexts at all.
    """
    contexts: List[str] = []
    directories = [os.path.join(system_dir, path) for path in SELINUX_DIRS]
    directories += context_dirs or []
    for s_dir in dict.fromkeys(os.path.abspath(path) for path in directories):
        for c_name in CONTEXT_FILES:
            contexts += _read_contexts(os.path.join(s_dir, c_name))

    if not contexts:
        return None
    if stock_labels:
        contexts += _stock_label_gaps(
            system_dir, contexts, stock_labels, mount_point
        )
    contexts += EXTRA_FILE_CONTEXTS

    out_dir = os.path.dirname(output_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(f"{ctx}\n" for ctx in contexts)

    return output_file
