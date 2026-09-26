"""
Archive extraction (ZIP, TAR, 7Z, RAR) with member filtering.
"""

from typing import Callable, List, Optional, Set
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

CHUNK = 1024 * 1024

ARCHIVE_EXTENSIONS = (
    '.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz',
    '.txz', '.tar.md5', '.7z', '.rar',
)
TAR_EXTENSIONS = ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz', '.tar.md5')

Filter = Optional[Callable[[str], bool]]


def is_archive(file_path: str) -> bool:
    if not os.path.isfile(file_path):
        return False
    if file_path.lower().endswith(ARCHIVE_EXTENSIONS):
        return True
    return zipfile.is_zipfile(file_path) or tarfile.is_tarfile(file_path)


def member_names(archive_path: str) -> Optional[List[str]]:
    """Basenames of a ZIP/TAR's files, or None for other archive types."""
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as zf:
                names = zf.namelist()
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, 'r:*') as tf:
                names = tf.getnames()
        else:
            return None
    except (OSError, zipfile.BadZipFile, tarfile.TarError):
        return None
    return [os.path.basename(n) for n in names]


def extract_archive(
    archive_path: str,
    output_dir: str,
    filter_func: Filter = None,
    logger=None
) -> List[str]:
    """
    Extracts archive_path into output_dir, keeping only members whose
    basename passes filter_func. Returns the extracted file paths.
    """
    os.makedirs(output_dir, exist_ok=True)

    if zipfile.is_zipfile(archive_path):
        return _extract_zip(archive_path, output_dir, filter_func, logger)

    lower = archive_path.lower()
    if tarfile.is_tarfile(archive_path) or lower.endswith(TAR_EXTENSIONS):
        try:
            return _extract_tar(archive_path, output_dir, filter_func,
                                logger)
        except tarfile.TarError:
            pass

    if lower.endswith('.7z'):
        return _extract_7z(archive_path, output_dir, filter_func)

    return _extract_host(archive_path, output_dir)


def _claim(output_dir: str, member: str, claimed: Set[str], logger):
    """Keep duplicate super pieces; skip other duplicate basenames."""
    base = os.path.basename(member)
    if base in claimed:
        if 'super' in base.lower() and base.lower().endswith(('.img', '.bin')):
            index = 1
            while True:
                folder = os.path.join(output_dir, f'super-members-{index}')
                if not os.path.exists(folder):
                    os.makedirs(folder)
                    return os.path.join(folder, base)
                index += 1
        if logger:
            logger(f"Warning: skipping {member}; {base} was already "
                   "extracted from another folder")
        return None
    claimed.add(base)
    return os.path.join(output_dir, base)


def _copy_member(src, target_path: str):
    with src, open(target_path, 'wb') as dst:
        shutil.copyfileobj(src, dst, CHUNK)


def _extract_zip(
    zip_path: str,
    output_dir: str,
    filter_func: Filter = None,
    logger=None
) -> List[str]:
    extracted = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        members = zf.infolist()

        # Pixel factory zips wrap the images in a nested image-*.zip.
        nested_images = [
            m for m in members
            if os.path.basename(m.filename).startswith('image-')
            and m.filename.endswith('.zip')
        ]
        has_system = any(m.filename.endswith('system.img') for m in members)
        if len(nested_images) == 1 and not has_system:
            nested = nested_images[0]
            if logger:
                logger(f"Unwrapping nested factory image {nested.filename}...")
            nested_tmp = os.path.join(output_dir, "nested_images.zip")
            _copy_member(zf.open(nested), nested_tmp)
            extracted = _extract_zip(
                nested_tmp, output_dir, filter_func, logger)
            try:
                os.remove(nested_tmp)
            except OSError:
                pass
            return extracted

        payloads = [
            m for m in members
            if os.path.basename(m.filename) == 'payload.bin'
        ]
        if payloads:
            target = os.path.join(output_dir, "payload.bin")
            _copy_member(zf.open(payloads[0]), target)
            return [target]

        claimed: Set[str] = set()
        for info in members:
            if info.is_dir():
                continue
            base = os.path.basename(info.filename)
            if not base or (filter_func and not filter_func(base)):
                continue
            target = _claim(output_dir, info.filename, claimed, logger)
            if target:
                _copy_member(zf.open(info), target)
                extracted.append(target)

    return extracted


def _extract_tar(
    tar_path: str,
    output_dir: str,
    filter_func: Filter = None,
    logger=None
) -> List[str]:
    extracted = []
    claimed: Set[str] = set()
    with tarfile.open(tar_path, 'r:*') as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            base = os.path.basename(member.name)
            if not base or (filter_func and not filter_func(base)):
                continue
            target = _claim(output_dir, member.name, claimed, logger)
            src = tf.extractfile(member) if target else None
            if src:
                _copy_member(src, target)
                extracted.append(target)

    return extracted


def _extract_7z(
    seven_z_path: str,
    output_dir: str,
    filter_func: Filter = None
) -> List[str]:
    if HAS_PY7ZR:
        try:
            with py7zr.SevenZipFile(seven_z_path, mode='r') as archive:
                targets = [
                    n for n in archive.getnames()
                    if not filter_func or filter_func(os.path.basename(n))
                ]
                if targets:
                    archive.extract(path=output_dir, targets=targets)
            return [os.path.join(output_dir, t) for t in targets]
        except Exception:
            pass

    return _extract_host(seven_z_path, output_dir)


def _host_extractors(archive_path: str):
    """(name, argv builder) for each archive tool on this host, best
    first."""
    seven_z = [(name, lambda a, o, t=path: [t, "x", "-y", f"-o{o}", a])
               for name, path in ((n, shutil.which(n))
                                  for n in ("7zz", "7z", "7za", "7zr"))
               if path]
    bsdtar = shutil.which("bsdtar")
    libarchive = ([("bsdtar", lambda a, o: [bsdtar, "-xf", a, "-C", o])]
                  if bsdtar else [])
    # Homebrew's and distros' 7-Zip builds leave out the non-free RAR
    # decoder; libarchive reads RAR and RAR5.
    if archive_path.lower().endswith('.rar'):
        return libarchive + seven_z
    return seven_z + libarchive


def _extract_host(archive_path: str, output_dir: str) -> List[str]:
    """
    Extracts with the first host tool that succeeds. Each attempt goes to
    a scratch directory that is only moved into output_dir on success, so
    a tool failing halfway leaves no truncated files behind.
    """
    tried = []
    for name, argv in _host_extractors(archive_path):
        scratch = tempfile.mkdtemp(prefix=".extract-", dir=output_dir)
        try:
            rc = subprocess.run(argv(archive_path, scratch),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).returncode
            if rc == 0:
                for entry in os.listdir(scratch):
                    target = os.path.join(output_dir, entry)
                    shutil.move(os.path.join(scratch, entry), target)
                return [os.path.join(root, f)
                        for root, _, files in os.walk(output_dir)
                        for f in files]
            tried.append(f"{name} (exit {rc})")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    raise RuntimeError(
        f"cannot extract {os.path.basename(archive_path)}: "
        + (", ".join(tried) + " failed" if tried
           else "no 7-Zip or bsdtar found"))
