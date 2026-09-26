"""
Strips vendor headers that precede partition images: Motorola/ASUS ext4
headers and SSSS/BFBF signature headers.
"""

import os
import shutil
import struct

from .formats import sparse

CHUNK = 1024 * 1024
EXT4_MAGIC = b'\x53\xef'
EXT4_MAGIC_OFFSET = 1080


def _remove(path: str):
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def clean_vendor_ext4_header(image_path: str, logger=None) -> bool:
    if not os.path.isfile(image_path) or os.path.getsize(image_path) < 132000:
        return False

    with open(image_path, 'rb') as f:
        magic_bytes = f.read(12)
        is_moto = b"MOTO" in magic_bytes
        if not is_moto and b"ASUS" not in magic_bytes:
            return False

        f.seek(0)
        idx = f.read(256 * 1024).find(EXT4_MAGIC)
        if idx < EXT4_MAGIC_OFFSET:
            return False

        offset = idx - EXT4_MAGIC_OFFSET
        if offset == 128055:
            offset = 131072
        if offset <= 0:
            return False

    if logger:
        vendor = "Motorola" if is_moto else "ASUS"
        logger(f"Stripping {vendor} header at offset {offset} "
               f"from {os.path.basename(image_path)}...")

    temp_path = f"{image_path}.clean.tmp"
    try:
        with open(image_path, 'rb') as src, open(temp_path, 'wb') as dst:
            src.seek(offset)
            shutil.copyfileobj(src, dst, CHUNK)
        shutil.move(temp_path, image_path)
        return True
    finally:
        _remove(temp_path)


def clean_signed_image(image_path: str, logger=None) -> bool:
    """Strips an SSSS/BFBF signature header, unsparsing the payload."""
    if not os.path.isfile(image_path) or os.path.getsize(image_path) < 16450:
        return False

    with open(image_path, 'rb') as f:
        magic = f.read(4)
        if magic not in (b"SSSS", b"BFBF"):
            return False

        if magic == b"SSSS":
            f.seek(60)
            length_field = f.read(4)
            if len(length_field) < 4:
                return False
            payload_offset = 64
            payload_len = struct.unpack('<I', length_field)[0]
        else:
            payload_offset = 0x4040
            payload_len = None

    if logger:
        logger(f"Stripping signed header ({magic.decode('latin1')}) "
               f"from {os.path.basename(image_path)}...")

    temp_path = f"{image_path}.signed.tmp"
    try:
        with open(image_path, 'rb') as src, open(temp_path, 'wb') as dst:
            src.seek(payload_offset)
            if payload_len:
                remaining = payload_len
                while remaining > 0:
                    chunk = src.read(min(remaining, CHUNK))
                    if not chunk:
                        break
                    dst.write(chunk)
                    remaining -= len(chunk)
            else:
                shutil.copyfileobj(src, dst, CHUNK)

        if sparse.is_sparse(temp_path):
            sparse.unsparse(temp_path, image_path)
        else:
            shutil.move(temp_path, image_path)
        return True
    finally:
        _remove(temp_path)
