import os
import platform
import shutil
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIN_ROOT = os.path.join(REPO_ROOT, "tools", "bin")

# Android builds of e2fsprogs install mke2fs as mke2fs.android; prefer it
# over a plain host mke2fs, which lacks the Android extensions.
TOOL_ALIASES = {
    "mke2fs": ("mke2fs.android", "mke2fs"),
}

REQUIRED_TOOLS = ("mke2fs", "e2fsdroid", "openssl")


def enable_case_sensitive(path: str) -> None:
    """Allow Linux file names that differ only in case on Windows."""
    if os.name != "nt":
        return
    from ctypes import WinDLL, WinError, byref, get_last_error, sizeof
    from ctypes.wintypes import DWORD, HANDLE, LPCWSTR

    kernel32 = WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = HANDLE
    path = os.path.abspath(path)

    def check(result, operation):
        if not result:
            error = WinError(get_last_error())
            if error.winerror == 5:
                advice = (
                    "Use an up-to-date Python and grant write attributes, "
                    "create files/folders and delete children permissions "
                    "on this directory."
                )
            else:
                advice = (
                    "Use a local NTFS directory on Windows 10/11, or "
                    "build in WSL's Linux filesystem."
                )
            error.strerror = (
                f"Cannot enable directory case sensitivity ({operation}): "
                f"{error.strerror}. {advice}"
            )
            error.filename = path
            raise error

    # Read/write attributes, sharing read/write/delete, on a directory.
    handle = kernel32.CreateFileW(
        LPCWSTR(path), DWORD(0x180), DWORD(7), None,
        DWORD(3), DWORD(0x02000000), None,
    )
    check(handle != HANDLE(-1).value, "CreateFileW")
    handle = HANDLE(handle)
    try:
        flags = DWORD()
        # FileCaseSensitiveInfo (23), FILE_CS_FLAG_CASE_SENSITIVE_DIR (1).
        check(
            kernel32.GetFileInformationByHandleEx(
                handle, 23, byref(flags), sizeof(flags)
            ),
            "GetFileInformationByHandleEx",
        )
        if not flags.value & 1:
            flags.value |= 1
            check(
                kernel32.SetFileInformationByHandle(
                    handle, 23, byref(flags), sizeof(flags)
                ),
                "SetFileInformationByHandle",
            )
    finally:
        kernel32.CloseHandle(handle)


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


def _configure_windows_output() -> None:
    os.environ["PYTHONIOENCODING"] = "utf-8:backslashreplace"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def configure_environment() -> None:
    """Configure native tool paths and Windows UTF-8 output."""
    system = platform.system()
    paths = [os.path.join(BIN_ROOT, system, platform.machine()), BIN_ROOT]
    if system == "Darwin":
        paths.extend(_brew_paths())
    elif system == "Windows":
        _configure_windows_output()
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
