"""Android dynamic partitions (super.img / LP metadata) extractor."""

from typing import Dict, List, Optional, Set
import hashlib
import os
import struct
import tempfile

from . import sparse

LP_METADATA_GEOMETRY_MAGIC = 0x616C4467
LP_METADATA_HEADER_MAGIC = 0x414C5030

LP_PARTITION_RESERVED_BYTES = 4096
LP_METADATA_GEOMETRY_SIZE = 4096
LP_SECTOR_SIZE = 512

LP_TARGET_TYPE_LINEAR = 0
LP_TARGET_TYPE_ZERO = 1

GEOMETRY_FORMAT = '<II32sIII'
HEADER_FORMAT = '<IHHI32sI32s12I'
HEADER_CHECKSUM = slice(12, 44)
TABLES_CHECKSUM = slice(48, 80)

MB = 1024 * 1024

# (geometry offset, reserved bytes before it). Normally 4096 reserved bytes
# precede the primary geometry, with the backup copy right after; some
# dumps have the reserved area stripped.
GEOMETRY_LOCATIONS = (
    (0, 0),
    (LP_PARTITION_RESERVED_BYTES, LP_PARTITION_RESERVED_BYTES),
    (LP_PARTITION_RESERVED_BYTES + LP_METADATA_GEOMETRY_SIZE,
     LP_PARTITION_RESERVED_BYTES),
)


class LpExtent:
    def __init__(self, num_sectors: int, target_type: int, target_data: int,
                 target_source: int):
        self.num_sectors = num_sectors
        self.target_type = target_type
        self.target_data = target_data
        self.target_source = target_source

    @property
    def num_bytes(self) -> int:
        return self.num_sectors * LP_SECTOR_SIZE

    @property
    def file_offset(self) -> int:
        return self.target_data * LP_SECTOR_SIZE


class LpPartition:
    def __init__(self, name: str, extents: List[LpExtent]):
        self.name = name
        self.extents = extents

    @property
    def num_bytes(self) -> int:
        return sum(ext.num_bytes for ext in self.extents)


def _table_entries(f, offset: int, count: int, entry_size: int,
                   min_size: int):
    f.seek(offset)
    raw = f.read(count * entry_size)
    for i in range(count):
        entry = raw[i * entry_size:(i + 1) * entry_size]
        if len(entry) >= min_size:
            yield entry[:min_size]


def _find_geometry(f, file_size: int):
    """Returns (reserved bytes before the geometry, geometry fields)."""
    for offset, reserved in GEOMETRY_LOCATIONS:
        if offset + 52 > file_size:
            continue
        f.seek(offset)
        fields = struct.unpack(GEOMETRY_FORMAT, f.read(52))
        if fields[0] == LP_METADATA_GEOMETRY_MAGIC:
            return reserved, fields
    return None


def _read_header(f, offset: int, max_size: int) -> Optional[bytes]:
    f.seek(offset)
    head = f.read(128)
    if (len(head) < 128
            or struct.unpack('<I', head[:4])[0] != LP_METADATA_HEADER_MAGIC):
        return None

    header_size = struct.unpack('<I', head[8:12])[0]
    if not 128 <= header_size <= LP_METADATA_GEOMETRY_SIZE:
        return None
    f.seek(offset)
    header = bytearray(f.read(header_size))
    if len(header) != header_size:
        return None
    checksum = bytes(header[HEADER_CHECKSUM])
    header[HEADER_CHECKSUM] = bytes(32)
    if hashlib.sha256(header).digest() != checksum:
        return None

    tables_size = struct.unpack('<I', head[44:48])[0]
    if tables_size > max_size - header_size:
        return None
    tables = f.read(tables_size)
    if hashlib.sha256(tables).digest() != head[TABLES_CHECKSUM]:
        return None
    return head


def read_lp_metadata(f) -> Optional[Dict[str, LpPartition]]:
    """Returns the partitions described by the first valid metadata slot."""
    f.seek(0, os.SEEK_END)
    file_size = f.tell()

    geometry = _find_geometry(f, file_size)
    if geometry is None:
        return None
    reserved, (_, _, _, max_size, slot_count, _) = geometry

    metadata_start = reserved + 2 * LP_METADATA_GEOMETRY_SIZE
    # Primary slots, then the backup copies that follow them.
    for slot in range(2 * slot_count):
        hdr_offset = metadata_start + slot * max_size
        hdr_data = _read_header(f, hdr_offset, max_size)
        if hdr_data is not None:
            break
    else:
        return None

    (
        _, _, _, header_size, _, _, _,
        part_offset, part_num_entries, part_entry_size,
        ext_offset, ext_num_entries, ext_entry_size,
        _, _, _, _, _, _,
    ) = struct.unpack(HEADER_FORMAT, hdr_data)
    tables_start = hdr_offset + header_size

    extents = [
        LpExtent(*struct.unpack('<QIQI', entry))
        for entry in _table_entries(f, tables_start + ext_offset,
                                    ext_num_entries, ext_entry_size, 24)
    ]

    partitions: Dict[str, LpPartition] = {}
    for entry in _table_entries(f, tables_start + part_offset,
                                part_num_entries, part_entry_size, 52):
        raw_name, _, first_ext, num_ext, _ = struct.unpack('<36sIIII', entry)
        name = raw_name.decode('utf-8', errors='ignore').rstrip('\x00')
        if name:
            partitions[name] = LpPartition(
                name, extents[first_ext:first_ext + num_ext])
    return partitions


def is_super_image(file_path: str) -> bool:
    if not os.path.isfile(file_path) or sparse.is_sparse(file_path):
        return False
    try:
        with open(file_path, 'rb') as f:
            return read_lp_metadata(f) is not None
    except OSError:
        return False


def _write_partition(f, part: LpPartition, out_path: str):
    with tempfile.TemporaryDirectory(
            prefix='lp-partition-', dir=os.path.dirname(out_path)) as scratch:
        candidate = os.path.join(scratch, 'partition.img')
        _copy_partition(f, part, candidate)
        os.replace(candidate, out_path)


def _copy_partition(f, part: LpPartition, out_path: str):
    with open(out_path, 'wb') as out_f:
        out_size = 0
        for ext in part.extents:
            if ext.target_type == LP_TARGET_TYPE_LINEAR:
                if ext.target_source != 0:
                    raise RuntimeError(
                        f"Unsupported LP block device for {part.name}")
                f.seek(ext.file_offset)
                remaining = ext.num_bytes
                while remaining > 0:
                    buf = f.read(min(remaining, MB))
                    if not buf:
                        raise RuntimeError(
                            f"Truncated LP extent for {part.name}")
                    out_f.write(buf)
                    remaining -= len(buf)
                    out_size += len(buf)
            elif ext.target_type == LP_TARGET_TYPE_ZERO:
                out_f.seek(ext.num_bytes, os.SEEK_CUR)
                out_size += ext.num_bytes
            else:
                raise RuntimeError(
                    f"Unsupported LP extent type for {part.name}")
        out_f.truncate(out_size)


def unpack_super(
    super_path: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None
) -> List[str]:
    """Extract dynamic partitions from a raw or sparse super.img."""
    if not os.path.isfile(super_path):
        return []

    os.makedirs(output_dir, exist_ok=True)
    temp_raw = None
    active_path = super_path
    supplied = None

    try:
        if sparse.is_sparse(super_path):
            if logger:
                logger("Unsparsing super.img...")
            fd, temp_raw = tempfile.mkstemp(
                prefix="super_raw_", suffix=".img", dir=output_dir)
            os.close(fd)
            supplied = []
            if not sparse.unsparse(super_path, temp_raw, supplied=supplied):
                if logger:
                    logger("Failed to unsparse super.img")
                return []
            active_path = temp_raw

        with open(active_path, 'rb') as f:
            partitions = read_lp_metadata(f)
            if partitions is None:
                if logger:
                    logger("No valid LP metadata found in super.img")
                return []

            extracted = []
            targets = ({p.lower() for p in target_partitions}
                       if target_partitions else None)

            for name, part in partitions.items():
                base_name = name[:-2] if name.endswith(('_a', '_b')) else name
                if (targets is not None and name.lower() not in targets
                        and base_name.lower() not in targets):
                    continue
                if part.num_bytes == 0:
                    continue
                if supplied is not None and not any(
                        left < ext.file_offset + ext.num_bytes
                        and right > ext.file_offset
                        for ext in part.extents
                        if ext.target_type == LP_TARGET_TYPE_LINEAR
                        for left, right in supplied):
                    continue
                # With both slots populated, slot a is the one postprocess
                # keeps; extracting b too only wastes time and disk.
                slot_a = partitions.get(base_name + '_a')
                if name.endswith('_b') and slot_a and slot_a.num_bytes:
                    continue

                if logger:
                    logger(f"Extracting partition {name} "
                           f"({part.num_bytes // MB} MB)...")

                out_path = os.path.join(output_dir, f"{name}.img")
                _write_partition(f, part, out_path)
                if os.path.getsize(out_path) > 0:
                    extracted.append(out_path)

            return extracted

    finally:
        if temp_raw and os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except OSError:
                pass
