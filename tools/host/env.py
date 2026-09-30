"""
Host tool discovery for the native binaries the build still needs.
"""

from typing import Optional
import os
import platform
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Android builds of e2fsprogs install mke2fs as mke2fs.android; prefer it
# over a plain host mke2fs, which lacks the Android extensions.
TOOL_ALIASES = {
    "mke2fs": ("mke2fs.android", "mke2fs"),
}

REQUIRED_TOOLS = ("mke2fs", "e2fsdroid", "openssl")


def _platform_bin():
    return os.path.join(
        REPO_ROOT, "tools", "bin", platform.system(), platform.machine()
    )


def _brew_paths():
    brew = shutil.which("brew")
    if not brew:
        return []
    try:
        prefix = subprocess.check_output([brew, "--prefix"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return []
    return [
        f"{prefix}/opt/e2fsprogs/sbin",
        f"{prefix}/opt/e2fsprogs/bin",
        f"{prefix}/opt/gpatch/libexec/gnubin",
        f"{prefix}/opt/openssl@3/bin",
        f"{prefix}/opt/openjdk@17/bin",
    ]


def configure_environment() -> None:
    """Prepends bundled, locally built and Homebrew tools to PATH."""
    paths = [_platform_bin(), os.path.join(REPO_ROOT, "tools", "bin")]
    if sys.platform == "darwin":
        paths.extend(_brew_paths())

    current = os.environ.get("PATH", "").split(os.pathsep)
    new = [p for p in paths if os.path.isdir(p)]
    if new:
        os.environ["PATH"] = os.pathsep.join(new + [p for p in current if p not in new])


def find_tool(tool_name: str) -> Optional[str]:
    """Checks an env override (e.g. MKE2FS, E2FSDROID), then PATH."""
    env_var = tool_name.upper().replace(".", "_").replace("-", "_")
    override = os.environ.get(env_var)
    if override and os.path.isfile(override):
        return override

    for name in TOOL_ALIASES.get(tool_name, (tool_name,)):
        found = shutil.which(name)
        if found:
            return found

    return None


def check_environment() -> None:
    """Raises RuntimeError if the image build tools are missing."""
    configure_environment()

    missing = [t for t in REQUIRED_TOOLS if not find_tool(t)]
    if not missing:
        return

    raise RuntimeError(
        f"Missing native image tools: {', '.join(missing)}. Run ./setup_host.py first."
    )
