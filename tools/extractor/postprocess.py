"""
Turns a staging directory of raw extractor output into canonical partition
images: rebuilds sdat/SIN/sparse-chunk images, unpacks super.img, unsparses,
strips vendor headers and normalizes names.
"""

from typing import Dict, List, Optional, Set, Tuple
import glob
import os
import re
import shutil
import tempfile

import lz4.frame

try:
    import zstandard
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

from .formats import sdat, sin, sparse, super as lp_super
from . import headers

ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
# Motorola split images: system.img_sparsechunk.0, super_sparsechunk.12, ...
SPARSECHUNK = re.compile(
    r'^(?P<part>[\w-]+?)(?:\.img)?_sparsechunk\.(?P<index>\d+)$')
IMAGE_EXTENSIONS = ('.img', '.bin', '.mbn', '.elf', '.ext4', '.raw')
FILENAME_SUFFIXES = (
    '_image.emmc.img', '_new.img', '.img.ext4', '.img.zst', '.zst', '.zstd',
    '-sign.img', '-p.img', '-p',
)


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def normalize_partition_filename(filename: str) -> str:
    name = filename.strip().lower()
    if name.endswith('.lz4'):
        name = name[:-4]

    for suffix in FILENAME_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)] + '.img'
            break

    for extension in IMAGE_EXTENSIONS:
        if name.endswith(extension):
            name = name[:-len(extension)] + '.img'
            break

    if name.endswith('_a.img'):
        name = name[:-6] + '.img'

    if not name.endswith('.img'):
        name += '.img'

    return name.lower()


def partition_of(filename: str) -> str:
    norm = normalize_partition_filename(filename)
    return norm[:-4] if norm.endswith('.img') else norm


def is_wanted(filename: str, targets: Set[str]) -> bool:
    """
    False for image files the pipeline would discard anyway: anything not
    named after a target partition or super. Containers, transfer lists,
    SIN files, sparse chunks etc. are always kept.
    """
    if not filename.lower().endswith(IMAGE_EXTENSIONS + ('.lz4',)):
        return True
    part = partition_of(filename)
    return part in targets or 'super' in part


def _staged(staging_dir: str, pattern: str):
    return glob.glob(os.path.join(staging_dir, pattern), recursive=True)


def _decompress_lz4(staging_dir: str, logger):
    for path in _staged(staging_dir, '**/*.lz4'):
        dest = path[:-4]
        if logger:
            logger(f"Decompressing {os.path.basename(path)}...")
        fd, temp_path = tempfile.mkstemp(
            prefix='lz4-', suffix='.img', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'wb') as out_f:
                with lz4.frame.open(path, 'rb') as in_f:
                    shutil.copyfileobj(in_f, out_f, 1024 * 1024)
            os.replace(temp_path, dest)
        except (OSError, RuntimeError) as error:
            _remove(temp_path)
            raise RuntimeError(f"Failed to decompress {path}: {error}")
        _remove(path)


def _rebuild_sdat(staging_dir: str, logger):
    for t_list in _staged(staging_dir, "**/*.transfer.list"):
        prefix = t_list[:-len('.transfer.list')]
        pieces = sdat.find_new_dat(prefix)
        if not pieces:
            continue
        part_name = os.path.basename(prefix).split('.')[0].lower()
        if logger:
            logger(f"Reconstructing {part_name}.img from sdat...")
        out_img = os.path.join(staging_dir, f"{part_name}.img")
        if sdat.sdat_to_img(t_list, out_img):
            for path in pieces + [t_list]:
                _remove(path)


def _merge_sparse_chunks(staging_dir: str, logger):
    groups: Dict[str, List[Tuple[int, str]]] = {}
    for path in glob.glob(os.path.join(staging_dir, "*")):
        match = SPARSECHUNK.match(os.path.basename(path))
        if match:
            groups.setdefault(match['part'], []).append(
                (int(match['index']), path))

    for part, chunks in groups.items():
        if logger:
            logger(f"Combining sparse chunks for {part}...")
        paths = [path for _, path in sorted(chunks)]
        sparse.unsparse(paths, os.path.join(staging_dir, f"{part}.img"))
        for path in paths:
            _remove(path)


def _is_zstd(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return f.read(4) == ZSTD_MAGIC
    except OSError:
        return False


def _finalize_image(img_path: str, dest_path: str, logger):
    """Moves img_path to dest_path, decompressing or unsparsing on the way."""
    name = os.path.basename(dest_path)
    if HAS_ZSTD and _is_zstd(img_path):
        if logger:
            logger(f"Decompressing zstd {name}...")
        try:
            with open(img_path, 'rb') as in_f, open(dest_path, 'wb') as out_f:
                zstandard.ZstdDecompressor().copy_stream(in_f, out_f)
            _remove(img_path)
        except Exception:
            shutil.move(img_path, dest_path)
    elif sparse.is_sparse(img_path):
        if logger:
            logger(f"Unsparsing {name}...")
        if sparse.unsparse(img_path, dest_path):
            _remove(img_path)
        else:
            raise RuntimeError(f"Failed to unsparse {name}")
    else:
        shutil.move(img_path, dest_path)

    headers.clean_vendor_ext4_header(dest_path, logger=logger)
    headers.clean_signed_image(dest_path, logger=logger)


def postprocess_extracted_images(
    staging_dir: str,
    output_dir: str,
    target_partitions: Optional[Set[str]] = None,
    logger=None
) -> Dict[str, str]:
    """
    Places canonical partition images from staging_dir into output_dir.
    Returns a mapping of partition name -> image path.
    """
    os.makedirs(output_dir, exist_ok=True)
    results: Dict[str, str] = {}
    targets = ({p.lower() for p in target_partitions}
               if target_partitions else None)

    for s_file in _staged(staging_dir, "**/*.sin"):
        sin.extract_sin(s_file, staging_dir, logger=logger)
        _remove(s_file)

    _decompress_lz4(staging_dir, logger)
    _rebuild_sdat(staging_dir, logger)
    _merge_sparse_chunks(staging_dir, logger)

    super_images = (_staged(staging_dir, "**/*super*.img")
                    + _staged(staging_dir, "**/*super*.bin"))
    for s_img in super_images:
        if not os.path.isfile(s_img):
            continue
        with tempfile.TemporaryDirectory(
                prefix='super-partitions-', dir=staging_dir) as scratch:
            extracted = lp_super.unpack_super(
                s_img, scratch, target_partitions=target_partitions,
                logger=logger)
            for path in extracted:
                name = normalize_partition_filename(os.path.basename(path))
                destination = os.path.join(staging_dir, name)
                candidate = os.path.join(scratch, 'converted.img')
                _finalize_image(path, candidate, logger)
                if os.path.exists(destination):
                    previous = os.path.join(scratch, 'previous.img')
                    _finalize_image(destination, previous, logger)
                    os.replace(previous, destination)
                # Factory supers can contain empty customization filesystems
                # alongside a populated version in a separate Open image.
                if (not os.path.exists(destination)
                        or os.path.getsize(candidate)
                        > os.path.getsize(destination)):
                    os.replace(candidate, destination)
        _remove(s_img)

    all_files = [os.path.join(root, f)
                 for root, _, files in os.walk(staging_dir) for f in files]
    for img_path in all_files:
        if not os.path.isfile(img_path):
            continue

        norm_name = normalize_partition_filename(os.path.basename(img_path))
        part_name = norm_name[:-4] if norm_name.endswith('.img') else norm_name
        if targets is not None and part_name not in targets:
            continue

        dest_path = os.path.join(output_dir, norm_name)
        _finalize_image(img_path, dest_path, logger)

        if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
            results[part_name] = dest_path

    return results
