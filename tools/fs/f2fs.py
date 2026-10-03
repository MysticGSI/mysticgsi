"""Read-only extraction of raw F2FS images."""

import os
import stat
import struct

from ..host import posix


BLOCK_SIZE = 4096
ADDRS_PER_BLOCK = 1018
INODE_ADDRS = 923
INODE_ADDR_OFFSET = 360
INODE_NID_OFFSET = 4052
NODE_FOOTER_OFFSET = 4072
NAT_ENTRIES_PER_BLOCK = BLOCK_SIZE // 9
CP_COMPACT_SUM_FLAG = 0x04
CP_UMOUNT_FLAG = 0x01
SUMMARY_JOURNAL_OFFSET = 3584
SUMMARY_JOURNAL_SIZE = 507
F2FS_MAGIC = 0xF2F52010
COMPRESS_ADDR = 0xFFFFFFFE
COMPRESS_HEADER_SIZE = 24
XATTR_MAGIC = 0xF2F52011
XATTR_SECURITY_INDEX = 6
XATTR_HEADER_SIZE = 24


class F2FSError(Exception):
    pass


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    return struct.unpack_from("<Q", data, offset)[0]


def _crc32(data):
    crc = F2FS_MAGIC
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc


class F2FSFilesystem:
    def __init__(self, path):
        self._file = open(path, "rb")
        self._size = os.fstat(self._file.fileno()).st_size
        try:
            self._load_superblock()
            self._load_checkpoint()
        except BaseException:
            self._file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._file.close()

    def _read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self._size:
            raise F2FSError("image is truncated")
        self._file.seek(offset)
        data = self._file.read(length)
        if len(data) != length:
            raise F2FSError("image is truncated")
        return data

    def _block(self, number):
        if not 0 <= number < self.block_count:
            raise F2FSError(f"block {number} is outside the image")
        return self._read(number * BLOCK_SIZE, BLOCK_SIZE)

    def _data_block(self, number):
        if not self.main_block <= number < self.block_count:
            raise F2FSError(f"invalid data block {number}")
        return self._block(number)

    def _load_superblock(self):
        primary = self._read(1024, 4096 - 1024)
        backup = self._read(5120, 4096 - 1024)
        sb = primary if _u32(primary, 0) == F2FS_MAGIC else backup
        if _u32(sb, 0) != F2FS_MAGIC:
            raise F2FSError("not an F2FS image")
        if _u32(sb, 16) != 12:
            raise F2FSError("unsupported F2FS block size")
        self.log_blocks_per_seg = _u32(sb, 20)
        if not 1 <= self.log_blocks_per_seg <= 12:
            raise F2FSError("invalid segment size")
        self.blocks_per_seg = 1 << self.log_blocks_per_seg
        self.block_count = _u64(sb, 36)
        self.cp_block = _u32(sb, 76)
        self.nat_block = _u32(sb, 84)
        self.main_block = _u32(sb, 92)
        self.root_ino = _u32(sb, 96)
        self.nat_segments = _u32(sb, 60)
        self.cp_payload = _u32(sb, 1664)
        self.flexible_inline_xattr = bool(_u32(sb, 2180) & 0x40)
        if (
            self.block_count * BLOCK_SIZE > self._size
            or not 0
            < self.cp_block
            < self.nat_block
            < self.main_block
            < self.block_count
            or not self.root_ino
            or self.nat_segments < 2
            or self.nat_segments % 2
        ):
            raise F2FSError("invalid F2FS geometry")

    def _checkpoint(self, start):
        first = self._block(start)
        total = _u32(first, 136)
        if not 2 <= total <= self.blocks_per_seg:
            return None
        last = self._block(start + total - 1)
        if (
            not self._valid_checkpoint_block(first)
            or not self._valid_checkpoint_block(last)
            or _u64(first, 0) != _u64(last, 0)
        ):
            return None
        return first

    @staticmethod
    def _valid_checkpoint_block(block):
        offset = _u32(block, 164)
        return 192 <= offset <= BLOCK_SIZE - 4 and _crc32(block[:offset]) == _u32(
            block, offset
        )

    def _load_checkpoint(self):
        candidates = []
        for start in (self.cp_block, self.cp_block + self.blocks_per_seg):
            cp = self._checkpoint(start)
            if cp is not None:
                candidates.append((_u64(cp, 0), start, cp))
        if not candidates:
            raise F2FSError("no complete F2FS checkpoint")
        _, self.cp_start, cp = max(candidates)
        self.cp_flags = _u32(cp, 132)
        self.cp_total = _u32(cp, 136)
        self.cp_sum_start = _u32(cp, 140)
        sit_size = _u32(cp, 156)
        nat_size = _u32(cp, 160)
        if (
            self.cp_payload + 2 > self.cp_total
            or 192 + sit_size + nat_size > (self.cp_payload + 1) * BLOCK_SIZE
        ):
            raise F2FSError("invalid checkpoint bitmap sizes")
        payload = b"".join(
            self._block(self.cp_start + offset) for offset in range(self.cp_payload + 1)
        )
        self.nat_bitmap = payload[192 + sit_size : 192 + sit_size + nat_size]
        nat_blocks = self.nat_segments // 2 * self.blocks_per_seg
        if len(self.nat_bitmap) * 8 < nat_blocks:
            raise F2FSError("incomplete NAT bitmap")
        self.max_nid = nat_blocks * NAT_ENTRIES_PER_BLOCK
        self.nat_journal = self._read_nat_journal()

    def _read_nat_journal(self):
        if not 0 < self.cp_sum_start < self.cp_total - 1:
            raise F2FSError("invalid checkpoint summary offset")
        if self.cp_flags & CP_COMPACT_SUM_FLAG:
            journal = self._block(self.cp_start + self.cp_sum_start)
        else:
            count = 6 if self.cp_flags & CP_UMOUNT_FLAG else 3
            block = self.cp_start + self.cp_total - count - 1
            journal = self._block(block)[SUMMARY_JOURNAL_OFFSET:]
        count = _u16(journal, 0)
        if count > (SUMMARY_JOURNAL_SIZE - 2) // 13:
            raise F2FSError("invalid NAT journal size")
        entries = {}
        for index in range(count):
            offset = 2 + index * 13
            nid = _u32(journal, offset)
            if not 0 < nid < self.max_nid:
                raise F2FSError("invalid NAT journal node id")
            entries[nid] = _u32(journal, offset + 9)
        return entries

    def _node(self, nid):
        if not 0 < nid < self.max_nid:
            raise F2FSError(f"invalid node id {nid}")
        address = self.nat_journal.get(nid)
        if address is None:
            block_index, entry = divmod(nid, NAT_ENTRIES_PER_BLOCK)
            segment, offset = divmod(block_index, self.blocks_per_seg)
            address = self.nat_block + segment * 2 * self.blocks_per_seg
            address += offset
            if self.nat_bitmap[block_index // 8] & (0x80 >> (block_index % 8)):
                address += self.blocks_per_seg
            address = _u32(self._block(address), entry * 9 + 5)
        node = self._data_block(address)
        if _u32(node, NODE_FOOTER_OFFSET) != nid:
            raise F2FSError(f"node {nid} has an invalid footer")
        return node

    def inode(self, nid):
        node = self._node(nid)
        if _u32(node, NODE_FOOTER_OFFSET + 4) != nid:
            raise F2FSError(f"node {nid} is not an inode")
        extra_size = _u16(node, INODE_ADDR_OFFSET) if node[3] & 0x20 else 0
        if extra_size % 4:
            raise F2FSError(f"inode {nid} has misaligned extra attributes")
        extra = extra_size // 4
        inline_xattr = 0
        if node[3] & 0x01:
            inline_xattr = _u16(node, 362) if self.flexible_inline_xattr else 50
        count = INODE_ADDRS - extra - inline_xattr
        if extra > 256 or count < 0:
            raise F2FSError(f"inode {nid} has invalid extra attributes")
        if _u32(node, 80) & 0x800:
            raise F2FSError(f"inode {nid} is encrypted")
        if _u32(node, 80) & 0x04 and extra_size < 36:
            raise F2FSError(f"inode {nid} has no compression attributes")
        return node, extra, count

    def selinux_label(self, inode):
        node, extra, count = inode
        xattr_nid = _u32(node, 76)
        inline_size = (INODE_ADDRS - extra - count) * 4
        if not inline_size and not xattr_nid:
            return None
        start = INODE_ADDR_OFFSET + (INODE_ADDRS - inline_size // 4) * 4
        data = node[start : start + inline_size]
        if xattr_nid:
            xattr_node = self._node(xattr_nid)
            if _u32(xattr_node, NODE_FOOTER_OFFSET + 4) != (
                _u32(node, NODE_FOOTER_OFFSET)
            ):
                raise F2FSError("xattr node has the wrong owner")
            data += xattr_node[:NODE_FOOTER_OFFSET]
        if len(data) < XATTR_HEADER_SIZE or data[:4] == b"\0" * 4:
            return None
        if _u32(data, 0) != XATTR_MAGIC:
            raise F2FSError("invalid F2FS xattr header")
        pos = XATTR_HEADER_SIZE
        while pos + 4 <= len(data):
            index, name_len, value_len = struct.unpack_from("<BBH", data, pos)
            if not index and not name_len and not value_len:
                return None
            end = pos + 4 + name_len + value_len
            if end > len(data):
                raise F2FSError("truncated F2FS xattr entry")
            if (
                index == XATTR_SECURITY_INDEX
                and data[pos + 4 : pos + 4 + name_len] == b"selinux"
            ):
                return data[pos + 4 + name_len : end].rstrip(b"\0")
            pos = (end + 3) & ~3
        raise F2FSError("missing F2FS xattr terminator")

    def _direct_addresses(self, nid, cluster_size):
        count = ADDRS_PER_BLOCK - ADDRS_PER_BLOCK % cluster_size
        if nid:
            yield from struct.unpack_from(f"<{count}I", self._node(nid))
        else:
            yield from (0 for _ in range(count))

    def _indirect_addresses(self, nid, cluster_size):
        nids = (
            struct.unpack_from("<1018I", self._node(nid))
            if nid
            else (0,) * ADDRS_PER_BLOCK
        )
        for child in nids:
            yield from self._direct_addresses(child, cluster_size)

    def _double_indirect_addresses(self, nid, cluster_size):
        nids = (
            struct.unpack_from("<1018I", self._node(nid))
            if nid
            else (0,) * ADDRS_PER_BLOCK
        )
        for child in nids:
            yield from self._indirect_addresses(child, cluster_size)

    def _addresses(self, node, extra, count, cluster_size=1):
        count -= count % cluster_size
        for index in range(count):
            yield _u32(node, INODE_ADDR_OFFSET + (extra + index) * 4)
        nids = struct.unpack_from("<5I", node, INODE_NID_OFFSET)
        for nid in nids[:2]:
            yield from self._direct_addresses(nid, cluster_size)
        for nid in nids[2:4]:
            yield from self._indirect_addresses(nid, cluster_size)
        yield from self._double_indirect_addresses(nids[4], cluster_size)

    def _file_blocks(self, node, extra, count):
        size = _u64(node, 16)
        needed = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
        capacity = (
            count + 2 * ADDRS_PER_BLOCK + 2 * ADDRS_PER_BLOCK**2 + ADDRS_PER_BLOCK**3
        )
        if needed > capacity:
            raise F2FSError("file exceeds F2FS address capacity")
        addresses = self._addresses(node, extra, count)
        for _ in range(needed):
            address = next(addresses)
            if address == COMPRESS_ADDR:
                raise F2FSError("compression marker in a plain file")
            yield None if address in (0, 0xFFFFFFFF) else address

    def _compressed_cluster(self, addresses, algorithm, flags):
        seen_gap = False
        blocks = []
        for address in addresses[1:]:
            if address in (0, 0xFFFFFFFF):
                seen_gap = True
            elif address == COMPRESS_ADDR or seen_gap:
                raise F2FSError("invalid compressed cluster addresses")
            else:
                blocks.append(self._data_block(address))
        if not blocks:
            raise F2FSError("compressed cluster has no data")
        packed = b"".join(blocks)
        length = _u32(packed, 0)
        if not 0 < length <= len(packed) - COMPRESS_HEADER_SIZE:
            raise F2FSError("invalid compressed cluster length")
        payload = packed[COMPRESS_HEADER_SIZE : COMPRESS_HEADER_SIZE + length]
        if flags & 1 and _crc32(payload) != _u32(packed, 4):
            raise F2FSError("compressed cluster checksum mismatch")
        expected = len(addresses) * BLOCK_SIZE
        if algorithm == 1:
            import lz4.block

            try:
                data = lz4.block.decompress(payload, uncompressed_size=expected)
            except lz4.block.LZ4BlockError as error:
                raise F2FSError(f"compressed cluster is invalid: {error}") from error
        elif algorithm == 2:
            import zstandard

            try:
                data = zstandard.ZstdDecompressor().decompress(
                    payload, max_output_size=expected
                )
            except zstandard.ZstdError as error:
                raise F2FSError(f"compressed cluster is invalid: {error}") from error
        else:
            raise F2FSError(f"unsupported F2FS compression algorithm {algorithm}")
        if len(data) != expected:
            raise F2FSError("compressed cluster has the wrong size")
        return data

    def _compressed_file_data(self, node, extra, count):
        log_size = node[393]
        if not 2 <= log_size <= 8:
            raise F2FSError("invalid F2FS compression cluster size")
        cluster_size = 1 << log_size
        algorithm = node[392]
        flags = _u16(node, 394)
        remaining = _u64(node, 16)
        direct_count = ADDRS_PER_BLOCK - ADDRS_PER_BLOCK % cluster_size
        inode_count = count - count % cluster_size
        capacity = (
            inode_count
            + 2 * direct_count
            + 2 * direct_count * ADDRS_PER_BLOCK
            + direct_count * ADDRS_PER_BLOCK**2
        )
        if (remaining + BLOCK_SIZE - 1) // BLOCK_SIZE > capacity:
            raise F2FSError("compressed file exceeds F2FS address capacity")
        addresses = self._addresses(node, extra, count, cluster_size)
        while remaining:
            cluster = [next(addresses) for _ in range(cluster_size)]
            if cluster[0] == COMPRESS_ADDR:
                data = self._compressed_cluster(cluster, algorithm, flags)
            else:
                if COMPRESS_ADDR in cluster:
                    raise F2FSError("misaligned compression marker")
                data = b"".join(
                    b"\0" * BLOCK_SIZE
                    if address in (0, 0xFFFFFFFF)
                    else self._data_block(address)
                    for address in cluster
                )
            length = min(remaining, len(data))
            yield data[:length]
            remaining -= length

    def _inline_data(self, node, extra, count):
        size = _u64(node, 16)
        capacity = (count - 1) * 4
        if size > capacity:
            raise F2FSError("inline data exceeds inode capacity")
        start = INODE_ADDR_OFFSET + (extra + 1) * 4
        return node[start : start + size]

    def file_data(self, inode):
        node, extra, count = inode
        if node[3] & 0x02:
            yield self._inline_data(node, extra, count)
            return
        if _u32(node, 80) & 0x04:
            yield from self._compressed_file_data(node, extra, count)
            return
        size = _u64(node, 16)
        for address in self._file_blocks(node, extra, count):
            length = min(size, BLOCK_SIZE)
            yield (
                b"\0" * length
                if address is None
                else (self._data_block(address)[:length])
            )
            size -= length

    def directory(self, inode):
        node, extra, count = inode
        if not stat.S_ISDIR(_u16(node, 0)):
            raise F2FSError("directory entry points to a non-directory")
        if node[3] & 0x04:
            capacity = (count - 1) * 4
            start = INODE_ADDR_OFFSET + (extra + 1) * 4
            entries = capacity * 8 // 153
            data = node[start : start + capacity]
            yield from self._dentries(data, entries)
        else:
            for block in self.file_data(inode):
                if len(block) != BLOCK_SIZE:
                    raise F2FSError("truncated directory block")
                yield from self._dentries(block, 214)

    @staticmethod
    def _dentries(data, entries):
        bitmap_size = (entries + 7) // 8
        entry_start = len(data) - entries * 19
        names_start = entry_start + entries * 11
        if entry_start < bitmap_size:
            raise F2FSError("invalid directory layout")
        index = 0
        while index < entries:
            if not data[index // 8] & (1 << (index % 8)):
                index += 1
                continue
            offset = entry_start + index * 11
            nid = _u32(data, offset + 4)
            length = _u16(data, offset + 8)
            slots = (length + 7) // 8
            if not nid or not 1 <= length <= 255 or index + slots > entries:
                raise F2FSError("invalid F2FS directory entry")
            name = data[names_start + index * 8 : names_start + index * 8 + length]
            if len(name) != length or b"/" in name or b"\0" in name:
                raise F2FSError("invalid F2FS file name")
            if name not in (b".", b".."):
                yield name, nid
            index += slots


def _extract_tree(fs, output_dir):
    root = fs.inode(fs.root_ino)
    if not stat.S_ISDIR(_u16(root[0], 0)):
        raise F2FSError("root inode is not a directory")
    os.makedirs(output_dir, exist_ok=True)
    pending = [(root, output_dir)]
    directory_modes = []
    visited = {fs.root_ino}
    while pending:
        directory, parent = pending.pop()
        for raw_name, nid in fs.directory(directory):
            name = os.fsdecode(raw_name)
            path = os.path.join(parent, name)
            inode = fs.inode(nid)
            mode = _u16(inode[0], 0)
            if os.path.lexists(path):
                raise F2FSError(f"colliding F2FS path: {path}")
            if stat.S_ISDIR(mode):
                if nid in visited:
                    raise F2FSError("directory loop")
                visited.add(nid)
                os.mkdir(path)
                directory_modes.append((path, stat.S_IMODE(mode)))
                pending.append((inode, path))
            elif stat.S_ISREG(mode):
                with open(path, "xb") as out:
                    for chunk in fs.file_data(inode):
                        out.write(chunk)
                os.chmod(path, stat.S_IMODE(mode))
            elif stat.S_ISLNK(mode):
                if _u64(inode[0], 16) > BLOCK_SIZE:
                    raise F2FSError("symbolic link target is too long")
                target = b"".join(fs.file_data(inode))
                if b"\0" in target:
                    raise F2FSError("invalid symbolic link target")
                posix.symlink(os.fsdecode(target), path)
            else:
                raise F2FSError(f"unsupported F2FS inode type: {path}")
    for path, mode in reversed(directory_modes):
        os.chmod(path, mode)
    os.chmod(output_dir, stat.S_IMODE(_u16(root[0], 0)))


def extract_f2fs(image_path: str, output_dir: str, logger=None) -> bool:
    """Extract files, directories and links without mounting the image."""
    log = logger or (lambda message: None)
    try:
        with F2FSFilesystem(image_path) as fs:
            _extract_tree(fs, output_dir)
    except (F2FSError, OSError, ValueError, struct.error) as error:
        log(f"Failed to extract F2FS image {image_path}: {error}")
        return False
    return True
