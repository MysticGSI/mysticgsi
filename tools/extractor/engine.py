"""
Firmware extraction entry point: identifies the package format, unpacks it
into a staging directory and hands the result to postprocess.
"""

from typing import List, Optional
import os
import shutil
import tempfile

from .. import config
from . import archive, postprocess
from .formats import (
    kdz, ozip, pac, payload, qfil, samsung, super as lp_super, uapp,
)

DIRECT_FORMATS = (
    (payload.is_payload, "A/B OTA payload.bin", payload.extract_payload),
    (lp_super.is_super_image, "Android super.img", lp_super.unpack_super),
    (pac.is_pac, "Unisoc PAC package", pac.extract_pac),
    (uapp.is_uapp, "Huawei UPDATE.APP package", uapp.extract_uapp),
    (kdz.is_kdz, "LG KDZ package", kdz.extract_kdz),
    (kdz.is_dz, "LG DZ package", kdz.extract_dz),
    (samsung.is_samsung_tar, "Samsung AP tar/lz4 package",
     samsung.extract_samsung_tar),
)

# Packages found inside an outer archive; super.img is left to postprocess.
NESTED_FORMATS = tuple(
    f for f in DIRECT_FORMATS if f[0] is not lp_super.is_super_image)


def _staged_files(staging_dir: str):
    return [os.path.join(root, f)
            for root, _, files in os.walk(staging_dir) for f in files]


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _member_filter(archive_path, targets):
    names = archive.member_names(archive_path)
    # QFIL piece names don't map to partitions; the XML does that later.
    if names is None or any(n.lower().startswith('rawprogram')
                            for n in names):
        return None
    return lambda name: postprocess.is_wanted(name, targets)


def _unpack_outer_archive(archive_path, staging_dir, targets, log):
    log(f"Unpacking archive {os.path.basename(archive_path)}...")
    before = set(_staged_files(staging_dir))
    archive.extract_archive(archive_path, staging_dir,
                            filter_func=_member_filter(archive_path, targets),
                            logger=log)

    for path in sorted(set(_staged_files(staging_dir)) - before):
        name = os.path.basename(path)
        if ozip.is_ozip(path):
            log(f"Decrypting nested OZIP {name}...")
            dec_zip = f"{path}.decrypted.zip"
            if not ozip.decrypt_ozip(path, dec_zip, logger=log):
                raise RuntimeError(f"Failed to decrypt nested OZIP {name}")
            _remove(path)
            _unpack_outer_archive(dec_zip, staging_dir, targets, log)
            _remove(dec_zip)
            continue
        nested = next((f for f in NESTED_FORMATS if f[0](path)), None)
        if nested:
            log(f"Extracting nested package {name}...")
            nested[2](path, staging_dir, target_partitions=targets,
                      logger=log)
            _remove(path)


def _stage(archive_path, staging_dir, targets, log) -> bool:
    """Unpacks archive_path into staging_dir; False if unrecognized."""
    name = os.path.basename(archive_path)
    direct = next((f for f in DIRECT_FORMATS if f[0](archive_path)), None)
    if direct:
        _, label, extract = direct
        log(f"Detected {label}")
        extract(archive_path, staging_dir, target_partitions=targets,
                logger=log)
    elif archive_path.endswith(('.img', '.bin')):
        log(f"Detected single partition image: {name}")
        shutil.copy2(archive_path, os.path.join(staging_dir, name))
    elif archive.is_archive(archive_path):
        _unpack_outer_archive(archive_path, staging_dir, targets, log)
        if qfil.is_qfil_dir(staging_dir):
            qfil.process_qfil(staging_dir, staging_dir,
                              target_partitions=targets, logger=log)
    else:
        log(f"Unsupported or unrecognized firmware format: {archive_path}")
        return False
    return True


def extract_firmware(
    archive_path: str,
    output_dir: str,
    target_partitions: Optional[List[str]] = None,
    logger=None
) -> int:
    """
    Extracts the partition images of any supported firmware package into
    output_dir. Returns 0 on success.
    """
    log = logger or print

    if not os.path.isfile(archive_path):
        log(f"Firmware file not found: {archive_path}")
        return 1

    targets = set(target_partitions or config.DEFAULT_PARTITIONS)
    os.makedirs(output_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(
            prefix="firmware_staging_", dir=output_dir) as staging_dir:
        name = os.path.basename(archive_path)
        log(f"Extracting firmware package: {name}...")

        try:
            if ozip.is_ozip(archive_path):
                log("Detected Oppo/Realme OZIP archive")
                dec_zip = os.path.join(staging_dir, "decrypted.zip")
                if not ozip.decrypt_ozip(archive_path, dec_zip, logger=log):
                    log("Failed to decrypt OZIP")
                    return 1
                return extract_firmware(
                    dec_zip, output_dir, target_partitions, logger)
            if not _stage(archive_path, staging_dir, targets, log):
                return 1
        except RuntimeError as e:
            log(f"Firmware extraction failed: {e}")
            return 1

        log("Post-processing extracted partition images...")
        extracted = postprocess.postprocess_extracted_images(
            staging_dir=staging_dir,
            output_dir=output_dir,
            target_partitions=targets,
            logger=log
        )

        has_system = os.path.isfile(os.path.join(output_dir, "system.img"))
        if "system" not in extracted and not has_system:
            log("Error: No system.img found in extracted firmware images! "
                f"Extracted: {list(extracted)}")
            return 1

        log("Firmware extraction successful! "
            f"Partitions: {', '.join(sorted(extracted))}")
        return 0
