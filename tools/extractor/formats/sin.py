"""Sony Xperia SIN payload extractor."""

from typing import Optional
import os
import re
import shutil
import struct
import tarfile

from . import sparse

try:
    import lz4.frame
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False

SPARSE_MAGIC = b'\x3a\xff\x26\xed'
LZ4_MAGIC = b'\x04\x22\x4d\x18'
EXT4_MAGIC = b'\x53\xef'
EXT4_MAGIC_OFFSET = 1080
SCAN_SIZE = 2 * 1024 * 1024
CHUNK = 1024 * 1024
# Signature/metadata members of tar-based SIN files.
TAR_METADATA = ('.asahdr', '.cms', '.crt', '.sig')

# system_ext_X-FLASH-ALL-C93B.sin -> system_ext
SIN_NAME = re.compile(r'^(?P<part>.+?)(?:_[^_]*FLASH.*)?\.sin$', re.I)


def partition_name(filename: str) -> str:
    match = SIN_NAME.match(filename)
    base = match['part'] if match else os.path.splitext(filename)[0]
    return base.lower()


def _plausible_ext4(buffer: bytes, start: int) -> bool:
    sb = buffer[start + 1024:start + 1024 + 64]
    if len(sb) < 64:
        return False
    inodes, blocks = struct.unpack('<II', sb[:8])
    log_block_size = struct.unpack('<I', sb[24:28])[0]
    return inodes > 0 and blocks > 0 and log_block_size <= 6


def find_payload_offset(f) -> Optional[int]:
    """Locates a sparse or ext4 payload within the first 2 MB."""
    f.seek(0)
    buffer = f.read(SCAN_SIZE)

    idx = buffer.find(SPARSE_MAGIC)
    if idx != -1:
        return idx

    idx = buffer.find(EXT4_MAGIC, EXT4_MAGIC_OFFSET)
    while idx != -1:
        start = idx - EXT4_MAGIC_OFFSET
        if _plausible_ext4(buffer, start):
            return start
        idx = buffer.find(EXT4_MAGIC, idx + 1)
    return None


def _is_tar(path: str) -> bool:
    # tarfile.is_tarfile() also accepts leading zero blocks as an empty
    # archive, so check for the ustar header instead.
    with open(path, 'rb') as f:
        f.seek(257)
        return f.read(5) == b'ustar'


def _extract_tar(sin_path: str, out_path: str) -> bool:
    """Newer SINs are tar archives of (optionally LZ4) payload pieces."""
    raw_path = f"{out_path}.sin.tmp"
    try:
        with tarfile.open(sin_path) as tar, open(raw_path, 'wb') as raw:
            members = sorted(
                (m for m in tar.getmembers() if m.isfile()
                 and not m.name.lower().endswith(TAR_METADATA)),
                key=lambda m: m.name)
            for member in members:
                src = tar.extractfile(member)
                if src.read(4) == LZ4_MAGIC:
                    if not HAS_LZ4:
                        raise RuntimeError(
                            "lz4 package is required for this SIN file")
                    src.seek(0)
                    src = lz4.frame.open(src)
                else:
                    src.seek(0)
                with src:
                    shutil.copyfileobj(src, raw, CHUNK)
        if not os.path.getsize(raw_path):
            return False
        return sparse.unsparse(raw_path, out_path)
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)


def extract_sin(
    sin_path: str,
    output_dir: str,
    logger=None
) -> Optional[str]:
    if not os.path.isfile(sin_path):
        return None

    fname = os.path.basename(sin_path)
    out_name = f"{partition_name(fname)}.img"
    out_path = os.path.join(output_dir, out_name)
    if logger:
        logger(f"Extracting Sony SIN {fname} -> {out_name}...")

    if _is_tar(sin_path):
        return out_path if _extract_tar(sin_path, out_path) else None

    with open(sin_path, 'rb') as f:
        offset = find_payload_offset(f)
        if offset is None:
            if logger:
                logger(f"Could not locate payload in {fname}")
            return None
        f.seek(offset)
        with open(out_path, 'wb') as out_f:
            out_f.truncate(sparse.expand(f, out_f))

    if os.path.getsize(out_path) > 0:
        return out_path
    return None
