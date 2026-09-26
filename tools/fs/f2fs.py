"""
F2FS extraction via a read-only loop mount (Linux only, needs root or sudo).
"""

import os
import subprocess
import sys
import tempfile


def _quiet(cmd):
    return subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def extract_f2fs(image_path: str, output_dir: str, logger=None) -> bool:
    if not os.path.isfile(image_path):
        return False

    if not sys.platform.startswith("linux"):
        if logger:
            logger(f"F2FS extraction is not supported on {sys.platform}")
        return False

    os.makedirs(output_dir, exist_ok=True)
    is_root = os.geteuid() == 0
    sudo = [] if is_root else ["sudo"]
    image = os.path.abspath(image_path)

    mountdir = tempfile.mkdtemp(prefix="f2fs_mnt_")
    try:
        if _quiet(sudo + ["mount", "-t", "f2fs", "-o", "loop,ro",
                          image, mountdir]) != 0:
            if logger:
                logger(f"Failed to loop-mount F2FS image {image_path}")
            return False
        try:
            subprocess.run(
                sudo + ["cp", "-a", f"{mountdir}/.", f"{output_dir}/"],
                check=True)
            if not is_root:
                _quiet(["sudo", "chown", "-hR",
                        f"{os.getuid()}:{os.getgid()}", output_dir])
            return True
        finally:
            _quiet(sudo + ["umount", mountdir])
    finally:
        try:
            os.rmdir(mountdir)
        except OSError:
            pass
    return True
