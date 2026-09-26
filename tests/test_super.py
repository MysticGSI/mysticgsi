import hashlib
import os
import struct
import zipfile

import pytest

from tools.extractor.formats import super as lp_super
from tools.extractor import extract_firmware
from tools.extractor.formats import sparse

SECTOR = 512
MAX_SIZE = 65536
SLOTS = 2
METADATA_START = 4096 + 2 * 4096
DATA_START = METADATA_START + 2 * SLOTS * MAX_SIZE


def _metadata(partitions):
    """partitions: [(name, [(num_sectors, type, target_sector)])]"""
    extents, entries = b"", b""
    for name, part_extents in partitions:
        first = len(extents) // 24
        for num_sectors, target_type, target in part_extents:
            extents += struct.pack(
                '<QIQI', num_sectors, target_type, target, 0)
        entries += struct.pack('<36sIIII', name.encode(), 0, first,
                               len(part_extents), 0)

    tables = entries + extents
    header = bytearray(struct.pack(
        '<IHHI32sI32s12I', lp_super.LP_METADATA_HEADER_MAGIC, 10, 0, 128,
        bytes(32), len(tables), hashlib.sha256(tables).digest(),
        0, len(partitions), 52,
        len(entries), len(extents) // 24, 24,
        0, 0, 48,
        0, 0, 64))
    header[12:44] = hashlib.sha256(header).digest()
    return bytes(header) + tables


def _build_super(path, partitions, payloads, corrupt_primary=None):
    geometry = struct.pack('<II32sIII', lp_super.LP_METADATA_GEOMETRY_MAGIC,
                           52, bytes(32), MAX_SIZE, SLOTS, 4096)
    metadata = _metadata(partitions)
    with open(path, 'wb') as f:
        for offset in (4096, 8192):
            f.seek(offset)
            f.write(geometry)
        for slot in range(2 * SLOTS):
            f.seek(METADATA_START + slot * MAX_SIZE)
            f.write(metadata)
        if corrupt_primary is not None:
            f.seek(METADATA_START + corrupt_primary)
            f.write(b'garbage')
        for sector, data in payloads:
            f.seek(sector * SECTOR)
            f.write(data)
        f.truncate(max(f.tell(), DATA_START + 64 * 1024))


def _unpack(tmp_path, **kwargs):
    system = os.urandom(8 * SECTOR)
    vendor_tail = os.urandom(2 * SECTOR)
    first = DATA_START // SECTOR
    image = tmp_path / "super.img"
    _build_super(image, [
        ("system_a", [(8, lp_super.LP_TARGET_TYPE_LINEAR, first)]),
        ("system_b", [(8, lp_super.LP_TARGET_TYPE_LINEAR, first + 16)]),
        ("vendor_a", [(4, lp_super.LP_TARGET_TYPE_ZERO, 0),
                      (2, lp_super.LP_TARGET_TYPE_LINEAR, first + 32)]),
        ("odm_a", []),
    ], [(first, system), (first + 32, vendor_tail)], **kwargs)

    out = tmp_path / "out"
    extracted = lp_super.unpack_super(str(image), str(out))
    names = sorted(os.path.basename(p) for p in extracted)
    return out, names, system, vendor_tail


def test_unpack_follows_extents_and_skips_redundant_slot(tmp_path):
    out, names, system, vendor_tail = _unpack(tmp_path)

    assert names == ["system_a.img", "vendor_a.img"]
    assert (out / "system_a.img").read_bytes() == system
    assert (out / "vendor_a.img").read_bytes() == \
        bytes(4 * SECTOR) + vendor_tail


def _sparse_super(raw, supplied_blocks):
    block_size = 4096
    chunks = []
    for index in range(len(raw) // block_size):
        if index < DATA_START // block_size or index in supplied_blocks:
            data = raw[index * block_size:(index + 1) * block_size]
            kind = sparse.CHUNK_TYPE_RAW
        else:
            data = b''
            kind = sparse.CHUNK_TYPE_DONT_CARE
        chunks.append(struct.pack('<2H2I', kind, 0, 1, 12 + len(data))
                      + data)
    return struct.pack(
        '<I4H4I', sparse.SPARSE_HEADER_MAGIC, 1, 0, 28, 12,
        block_size, len(raw) // block_size, len(chunks), 0) + b''.join(chunks)


@pytest.mark.parametrize('reverse', [False, True])
def test_archive_combines_supers_with_different_layouts(tmp_path, reverse):
    first = DATA_START // SECTOR
    system = os.urandom(4096)
    product = os.urandom(8192)
    mi = os.urandom(8192)
    placeholder = os.urandom(4096)
    image = tmp_path / 'raw.img'
    _build_super(image, [
        ('system_a', [(8, lp_super.LP_TARGET_TYPE_LINEAR, first)]),
        ('tr_product_a', [(8, lp_super.LP_TARGET_TYPE_LINEAR, first + 8)]),
        ('tr_mi_a', [(16, lp_super.LP_TARGET_TYPE_LINEAR, first + 16)]),
    ], [(first, system), (first + 8, placeholder), (first + 16, mi)])
    raw = image.read_bytes()
    block = DATA_START // 4096
    _build_super(image, [
        ('system_a', [(8, lp_super.LP_TARGET_TYPE_LINEAR, first)]),
        ('tr_product_a', [(16, lp_super.LP_TARGET_TYPE_LINEAR, first + 32)]),
        ('tr_mi_a', [(8, lp_super.LP_TARGET_TYPE_LINEAR, first + 48)]),
    ], [(first + 32, product), (first + 48, placeholder)])
    members = [
        ('Firmware/Open/super.img', _sparse_super(
            image.read_bytes(), {block + 4, block + 5, block + 6})),
        ('Firmware/super.img', _sparse_super(
            raw, {block, block + 1, block + 2, block + 3})),
    ]
    if reverse:
        members.reverse()
    source = tmp_path / 'firmware.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        for name, data in members:
            archive.writestr(name, data)
    output = tmp_path / 'out'

    assert extract_firmware(str(source), str(output)) == 0
    assert (output / 'system.img').read_bytes() == system
    assert (output / 'tr_product.img').read_bytes() == product
    assert (output / 'tr_mi.img').read_bytes() == mi


@pytest.mark.parametrize('payload_blocks', [1, 2])
def test_archive_extracts_honor_system_with_separate_metadata(
        tmp_path, payload_blocks):
    metadata = tmp_path / 'super_metadata.img'
    _build_super(metadata, [
        ('system_a', [(16, lp_super.LP_TARGET_TYPE_LINEAR, 2048)]),
    ], [])
    system = bytearray(payload_blocks * 4096)
    system[1024:1028] = b'\xe2\xe1\xf5\xe0'
    source = tmp_path / 'firmware.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.write(metadata, 'Firmware/super_metadata.img')
        archive.writestr('Firmware/super.img',
                         _sparse_super(system, set()))

    output = tmp_path / 'out'
    result = extract_firmware(str(source), str(output),
                              logger=lambda message: None)

    if payload_blocks == 2:
        assert result == 0
        assert (output / 'system.img').read_bytes() == system
    else:
        assert result == 1
        assert not (output / 'system.img').exists()


# 60 lands in the header, 200 in the partition/extent tables.
@pytest.mark.parametrize("corrupt_at", [60, 200])
def test_corrupt_primary_metadata_falls_back_to_next_slot(tmp_path,
                                                          corrupt_at):
    out, names, system, _ = _unpack(tmp_path, corrupt_primary=corrupt_at)

    assert names == ["system_a.img", "vendor_a.img"]
    assert (out / "system_a.img").read_bytes() == system


def test_truncated_extent_preserves_previous_partition(tmp_path):
    first = DATA_START // SECTOR
    image = tmp_path / 'super.img'
    _build_super(image, [
        ('system_a', [(8, lp_super.LP_TARGET_TYPE_LINEAR, first)]),
    ], [(first, b'a' * SECTOR)])
    with image.open('r+b') as stream:
        stream.truncate(DATA_START + SECTOR)
    output = tmp_path / 'out'
    output.mkdir()
    previous = output / 'system_a.img'
    previous.write_bytes(b'previous image')

    with pytest.raises(RuntimeError, match='Truncated LP extent'):
        lp_super.unpack_super(str(image), str(output))

    assert previous.read_bytes() == b'previous image'
    assert list(output.iterdir()) == [previous]
