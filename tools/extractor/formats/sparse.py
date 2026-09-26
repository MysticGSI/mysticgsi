"""Android sparse image to raw image converter."""

from typing import List, Optional, Tuple, Union
import os
import struct
import tempfile

SPARSE_HEADER_MAGIC = 0xED26FF3A
SPARSE_HEADER_FORMAT = '<I4H4I'
SPARSE_HEADER_SIZE = 28

CHUNK_HEADER_FORMAT = '<2H2I'
CHUNK_HEADER_SIZE = 12

CHUNK_TYPE_RAW = 0xCAC1
CHUNK_TYPE_FILL = 0xCAC2
CHUNK_TYPE_DONT_CARE = 0xCAC3
CHUNK_TYPE_CRC32 = 0xCAC4

BUFFER_SIZE = 1024 * 1024


def is_sparse(file_path: str) -> bool:
    try:
        with open(file_path, 'rb') as f:
            magic = struct.unpack('<I', f.read(4))[0]
            return magic == SPARSE_HEADER_MAGIC
    except (OSError, struct.error):
        return False


def _copy(in_f, out_f, size: int, buffer_size: int) -> int:
    written = 0
    while written < size:
        buf = in_f.read(min(size - written, buffer_size))
        if not buf:
            break
        out_f.write(buf)
        written += len(buf)
    return written


def _copy_all(in_f, out_f, buffer_size: int) -> int:
    written = 0
    while True:
        buf = in_f.read(buffer_size)
        if not buf:
            return written
        out_f.write(buf)
        written += len(buf)


def _fill(out_f, pattern: bytes, size: int, buffer_size: int) -> int:
    expanded = pattern * (min(buffer_size, size) // 4)
    written = 0
    while written < size:
        to_write = min(size - written, len(expanded))
        out_f.write(expanded[:to_write])
        written += to_write
    return written


def _write_chunks(in_f, out_f, header, buffer_size: int, supplied=None):
    (_, _, _, file_hdr_sz, chunk_hdr_sz, blk_sz, _, total_chunks, _) = header
    written_blocks = 0
    output_start = out_f.tell()

    if file_hdr_sz > SPARSE_HEADER_SIZE:
        in_f.seek(file_hdr_sz - SPARSE_HEADER_SIZE, os.SEEK_CUR)

    for _ in range(total_chunks):
        chunk_hdr = in_f.read(CHUNK_HEADER_SIZE)
        if len(chunk_hdr) < CHUNK_HEADER_SIZE:
            raise RuntimeError("Truncated sparse chunk header")

        chunk_type, _, chunk_sz, total_sz = struct.unpack(
            CHUNK_HEADER_FORMAT, chunk_hdr)
        if chunk_hdr_sz > CHUNK_HEADER_SIZE:
            in_f.seek(chunk_hdr_sz - CHUNK_HEADER_SIZE, os.SEEK_CUR)

        data_bytes = chunk_sz * blk_sz
        if total_sz < chunk_hdr_sz:
            raise RuntimeError("Invalid sparse chunk size")
        if chunk_type == CHUNK_TYPE_RAW:
            if total_sz - chunk_hdr_sz != data_bytes:
                raise RuntimeError("Invalid sparse RAW chunk size")
            if _copy(in_f, out_f, data_bytes, buffer_size) != data_bytes:
                raise RuntimeError("Truncated sparse RAW chunk")
        elif chunk_type == CHUNK_TYPE_FILL:
            if total_sz - chunk_hdr_sz != 4:
                raise RuntimeError("Invalid sparse FILL chunk size")
            pattern = in_f.read(4)
            if len(pattern) != 4:
                raise RuntimeError("Truncated sparse FILL chunk")
            _fill(out_f, pattern, data_bytes, buffer_size)
        elif chunk_type == CHUNK_TYPE_DONT_CARE:
            if total_sz != chunk_hdr_sz:
                raise RuntimeError("Invalid sparse DONT_CARE chunk size")
            out_f.seek(data_bytes, os.SEEK_CUR)
        elif chunk_type == CHUNK_TYPE_CRC32:
            if chunk_sz or total_sz - chunk_hdr_sz != 4:
                raise RuntimeError("Invalid sparse CRC32 chunk size")
            if len(in_f.read(4)) != 4:
                raise RuntimeError("Truncated sparse CRC32 chunk")
        else:
            raise RuntimeError(f"Unknown sparse chunk type: {chunk_type}")
        if supplied is not None and chunk_type in (
                CHUNK_TYPE_RAW, CHUNK_TYPE_FILL):
            supplied.append((output_start + written_blocks * blk_sz,
                             output_start
                             + (written_blocks + chunk_sz) * blk_sz))
        written_blocks += chunk_sz
    if written_blocks != header[6]:
        raise RuntimeError("Sparse block count does not match header")


def expand(in_f, out_f, offset: int = 0,
           buffer_size: int = BUFFER_SIZE, supplied=None) -> int:
    """
    Writes the sparse image read from in_f (positioned at its header) into
    out_f at offset, or copies in_f verbatim if it isn't sparse. Returns
    the end offset of the image; the caller truncates out_f to its size.
    """
    start = in_f.tell()
    header_data = in_f.read(SPARSE_HEADER_SIZE)
    out_f.seek(offset)
    header = None
    if len(header_data) == SPARSE_HEADER_SIZE:
        header = struct.unpack(SPARSE_HEADER_FORMAT, header_data)
    if header is None or header[0] != SPARSE_HEADER_MAGIC:
        if header_data[:4] == struct.pack('<I', SPARSE_HEADER_MAGIC):
            raise RuntimeError("Truncated sparse image header")
        in_f.seek(start)
        _copy_all(in_f, out_f, buffer_size)
        if supplied is not None:
            supplied.append((offset, out_f.tell()))
        return out_f.tell()

    if (header[1] != 1 or header[3] < SPARSE_HEADER_SIZE
            or header[4] < CHUNK_HEADER_SIZE
            or header[5] <= 0 or header[5] % 4 or buffer_size < 4):
        raise RuntimeError("Invalid sparse image header")
    _write_chunks(in_f, out_f, header, buffer_size, supplied)
    blk_sz, total_blks = header[5], header[6]
    return max(out_f.tell(), offset + blk_sz * total_blks)


def unsparse(
    input_files: Union[str, List[str]],
    output_file: str,
    buffer_size: int = BUFFER_SIZE,
    supplied: Optional[List[Tuple[int, int]]] = None
) -> bool:
    """
    Unsparse one image, or a list of split chunks (e.g. Motorola
    sparsechunks), into a single raw image. Non-sparse inputs are appended.
    If supplied is given, append output ranges backed by RAW/FILL chunks.
    """
    if isinstance(input_files, str):
        input_files = [input_files]
    if not input_files or not all(map(os.path.isfile, input_files)):
        return False

    out_dir = os.path.dirname(output_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    end = 0
    with tempfile.TemporaryDirectory(
            prefix="unsparse-", dir=out_dir or ".") as staging:
        converted = os.path.join(staging, "image.img")
        with open(converted, 'wb') as out_f:
            for in_file in input_files:
                with open(in_file, 'rb') as in_f:
                    # Each sparse file describes the whole image and skips
                    # the regions supplied by the other split chunks.
                    offset = 0 if is_sparse(in_file) else end
                    end = max(end, expand(in_f, out_f, offset, buffer_size,
                                          supplied))
            out_f.truncate(end)
        if end == 0:
            return False
        os.replace(converted, output_file)

    return os.path.getsize(output_file) > 0
