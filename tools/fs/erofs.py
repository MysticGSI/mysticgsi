"""
EROFS extraction via the host's extract.erofs or fsck.erofs.
"""

from typing import Optional, Tuple
import os
import shutil
import subprocess
import sys

TOOLS = ("extract.erofs", "fsck.erofs")
BREW_PREFIXES = ("/opt/homebrew/bin", "/usr/local/bin")


def find_erofs_tool() -> Optional[Tuple[str, str]] | None:
    """Returns (path, tool name) of the first available EROFS extractor."""
    for name in TOOLS:
        found = shutil.which(name)
        if found:
            return found, name

    # Homebrew's bin may be missing from PATH when not launched from a shell.
    if sys.platform == "darwin":
        for prefix in BREW_PREFIXES:
            for name in TOOLS:
                path = os.path.join(prefix, name)
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path, name

    return None


def _extracted(cmd, output_dir: str) -> bool:
    rc = subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return (rc == 0 and os.path.isdir(output_dir)
            and bool(os.listdir(output_dir)))


def extract_erofs(image_path: str, output_dir: str, logger=None) -> bool:
    if not os.path.isfile(image_path):
        return False

    tool = find_erofs_tool()
    if tool is None:
        if logger:
            logger("No EROFS extraction tool found. "
                   "Please install erofs-utils:\n"
                   "  macOS: brew install erofs-utils\n"
                   "  Linux: sudo apt install erofs-utils "
                   "(or dnf/pacman install erofs-utils)")
        return False

    tool_path, tool_name = tool
    os.makedirs(output_dir, exist_ok=True)
    image = os.path.abspath(image_path)
    out = os.path.abspath(output_dir)

    if tool_name == "fsck.erofs":
        return _extracted([tool_path, f"--extract={out}", image], output_dir)

    # extract.erofs writes into <out>/<image name>, so aim it at the parent
    # first; some builds write straight into -o instead.
    cmd = [tool_path, "-x", "-i", image, "-o"]
    return (_extracted(cmd + [os.path.dirname(out)], output_dir)
            or _extracted(cmd + [out], output_dir))
