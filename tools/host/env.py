import os
import platform
import shutil
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIN_ROOT = os.path.join(REPO_ROOT, "tools", "bin")

# Android builds of e2fsprogs install mke2fs as mke2fs.android; prefer it
# over a plain host mke2fs, which lacks the Android extensions.
TOOL_ALIASES = {
    "mke2fs": ("mke2fs.android", "mke2fs"),
}

REQUIRED_TOOLS = ("mke2fs", "e2fsdroid", "openssl")


def _brew_paths() -> list[str]:
    brew = shutil.which("brew")
    if not brew:
        return []
    try:
        prefix = subprocess.check_output([brew, "--prefix"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return []
    return [
        f"{prefix}/opt/gpatch/libexec/gnubin",
        f"{prefix}/opt/openssl@3/bin",
        f"{prefix}/opt/openjdk@17/bin",
    ]


def configure_environment() -> None:
    """Put bundled and platform tools before the existing PATH."""
    system = platform.system()
    paths = [os.path.join(BIN_ROOT, system, platform.machine()), BIN_ROOT]
    if system == "Darwin":
        paths.extend(_brew_paths())
    elif system == "Windows":
        paths.extend(
            [
                r"C:\Program Files\Git\usr\bin",
                r"C:\Program Files\7-Zip",
            ]
        )

    current = os.environ.get("PATH", "").split(os.pathsep)
    paths = [p for p in paths if os.path.isdir(p)]
    if paths:
        remaining = [p for p in current if p not in paths]
        os.environ["PATH"] = os.pathsep.join(paths + remaining)


def find_tool(tool_name: str) -> str | None:
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
    if missing:
        raise RuntimeError(
            f"Missing native image tools: {', '.join(missing)}. Run ./setup_host.py first."
        )
