"""Android A/B OTA payload.bin extractor."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set
import bz2
import lzma
import os
import struct

try:
    import zstandard

    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

try:
    from . import update_metadata_pb2 as pb
except ImportError:
    pb = None

PAYLOAD_MAGIC = b"CrAU"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
BZ2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"

# InstallOperation.Type values from update_metadata.proto.
OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_ZERO = 6
OP_DISCARD = 7
OP_REPLACE_XZ = 8
OP_REPLACE_ZSTD = 14
FULL_OPS = {
    OP_REPLACE,
    OP_REPLACE_BZ,
    OP_REPLACE_XZ,
    OP_REPLACE_ZSTD,
    OP_ZERO,
    OP_DISCARD,
}


class PayloadError(RuntimeError):
    pass


def is_payload(file_path: str, offset: int = 0) -> bool:
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            f.seek(offset)
            return f.read(4) == PAYLOAD_MAGIC
    except OSError:
        return False


def _zstd_decompress(data: bytes) -> bytes:
    if not HAS_ZSTD:
        raise PayloadError("zstandard package required for ZSTD payload extraction")
    # Vendor streams may omit the content size, so decompress as a stream.
    return zstandard.ZstdDecompressor().decompressobj().decompress(data)


def _sniff_replace(data: bytes) -> bytes:
    """
    Some vendors (e.g. Vivo/OriginOS) store compressed blobs in plain
    REPLACE ops. Decode by magic, keeping the raw bytes if the magic turns
    out to be a coincidence.
    """
    for magic, decompress in (
        (ZSTD_MAGIC, _zstd_decompress),
        (BZ2_MAGIC, bz2.decompress),
        (XZ_MAGIC, lzma.decompress),
    ):
        if data.startswith(magic):
            try:
                return decompress(data)
            except PayloadError:
                raise
            except Exception:
                return data
    return data


DECODERS = {
    OP_REPLACE: _sniff_replace,
    OP_REPLACE_BZ: bz2.decompress,
    OP_REPLACE_XZ: lzma.decompress,
    OP_REPLACE_ZSTD: _zstd_decompress,
}


def _check_op(op, partition: str):
    # An op type this proto doesn't know parses as unset instead of failing.
    if not op.HasField("type"):
        raise PayloadError(
            f"Unknown operation type in {partition}; payload.bin is newer "
            "than this extractor supports"
        )
    if op.src_extents or op.type not in FULL_OPS:
        op_name = pb.InstallOperation.Type.Name(op.type)
        raise PayloadError(
            f"{op_name} operation in {partition}: this is an "
            "incremental OTA, which needs the source build. "
            "Use a full OTA package instead."
        )


def _workers() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return os.cpu_count() or 1


def _decode(op_type, data: bytes) -> bytes:
    return DECODERS[op_type](data)


def _extract_partition(
    f, part, out_path, data_offset, block_size, pool, window
):
    """
    Reads op data sequentially and decompresses it on the pool: lzma, bz2
    and zstandard release the GIL, so ops decode in parallel. Results are
    written in op order, with at most `window` ops in flight.
    """
    for op in part.operations:
        _check_op(op, part.partition_name)

    def write(op, data):
        pos = 0
        for ext in op.dst_extents:
            size = ext.num_blocks * block_size
            out_f.seek(ext.start_block * block_size)
            out_f.write(data[pos : pos + size].ljust(size, b"\x00"))
            pos += size

    with open(out_path, "wb") as out_f:
        end = 0
        pending = deque()
        for op in part.operations:
            for ext in op.dst_extents:
                end = max(end, (ext.start_block + ext.num_blocks) * block_size)
            # ZERO/DISCARD blocks are left as holes, which read as zeros.
            if op.type in (OP_ZERO, OP_DISCARD):
                continue

            f.seek(data_offset + op.data_offset)
            data = f.read(op.data_length)
            pending.append((op, pool.submit(_decode, op.type, data)))
            if len(pending) >= window:
                done_op, fut = pending.popleft()
                write(done_op, fut.result())

        while pending:
            done_op, fut = pending.popleft()
            write(done_op, fut.result())

        if part.new_partition_info.size > 0:
            end = part.new_partition_info.size
        out_f.truncate(end)


def extract_payload(
    payload_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None,
    offset: int = 0,
) -> List[str]:
    """
    Extracts full-OTA partitions from payload.bin. `offset` locates the
    payload inside a larger file, e.g. stored uncompressed in an OTA zip.
    """
    if not is_payload(payload_path, offset):
        if logger:
            logger(f"Not a valid payload.bin: {payload_path}")
        return []

    if pb is None:
        raise PayloadError("protobuf package required for payload.bin")

    os.makedirs(output_dir, exist_ok=True)
    extracted: List[str] = []
    targets = {p.lower() for p in target_partitions} if target_partitions else None

    workers = _workers()
    with open(payload_path, "rb") as f, ThreadPoolExecutor(workers) as pool:
        f.seek(offset + len(PAYLOAD_MAGIC))
        version = struct.unpack(">Q", f.read(8))[0]
        manifest_size = struct.unpack(">Q", f.read(8))[0]
        metadata_sig_size = 0
        if version >= 2:
            metadata_sig_size = struct.unpack(">I", f.read(4))[0]

        manifest_raw = f.read(manifest_size)
        if len(manifest_raw) != manifest_size:
            if logger:
                logger("Truncated payload manifest")
            return []

        f.seek(metadata_sig_size, os.SEEK_CUR)
        data_offset = f.tell()

        manifest = pb.DeltaArchiveManifest()
        manifest.ParseFromString(manifest_raw)
        # update_engine treats old_partition_info as the sole sign of a
        # delta. minor_version isn't one: partial updates, as in OnePlus
        # and OPPO full OTAs, set it while replacing whole partitions.
        delta = next(
            (
                p.partition_name
                for p in manifest.partitions
                if p.HasField("old_partition_info")
            ),
            None,
        )
        if delta:
            raise PayloadError(
                f"{delta} has source partition info: payload.bin is an "
                "incremental OTA, which needs the source build. "
                "Use a full OTA package instead."
            )
        block_size = manifest.block_size

        for part in manifest.partitions:
            name = part.partition_name
            if targets is not None and name.lower() not in targets:
                continue

            out_path = os.path.join(output_dir, f"{name}.img")
            if logger:
                logger(f"Extracting payload partition {name}...")
            _extract_partition(
                f, part, out_path, data_offset, block_size, pool, workers * 4
            )

            if os.path.getsize(out_path) > 0:
                extracted.append(out_path)

    return extracted
