#!/usr/bin/env python3
"""
Installs MysticGSI's dependencies on macOS, Debian/Ubuntu, Arch and NixOS.

  ./setup_host.py          runtime dependencies
  ./setup_host.py --dev    plus pytest and flake8

Safe to re-run: package managers skip what is installed, and the native
image tools are only rebuilt when missing.
"""

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
VENV_PYTHON = os.path.join(VENV, "bin", "python")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
MIN_PYTHON = (3, 10)
APKTOOL_RELEASE = ("https://api.github.com/repos/iBotPeaches/Apktool/"
                   "releases/latest")

BREW_PACKAGES = [
    "python@3.13", "cmake", "ninja", "pkgconf", "erofs-utils", "brotli",
    "lz4", "pcre2", "libusb", "zstd", "protobuf", "aria2", "apktool",
    "gpatch",
]

APT_PACKAGES = [
    "python3", "python3-venv", "python3-pip", "erofs-utils", "aria2",
    "patch", "default-jre-headless", "curl", "ca-certificates",
    "build-essential", "cmake", "ninja-build", "pkg-config", "perl",
    "golang-go", "libgtest-dev", "libusb-1.0-0-dev", "libpcre2-dev",
    "libprotobuf-dev", "protobuf-compiler", "libbrotli-dev", "liblz4-dev",
    "libzstd-dev",
]
PACMAN_PACKAGES = [
    "python", "python-pip", "erofs-utils", "aria2", "patch",
    "android-tools", "curl",
]
# The source package builds apktool with its own JDK and Gradle.
AUR_APKTOOL = "android-apktool-bin"

NATIVE_TOOLS_CHECK = """
import sys
from tools.host import check_environment
try:
    check_environment()
except RuntimeError:
    sys.exit(3)
"""


def log(message):
    print(f"\033[1m==> {message}\033[0m", flush=True)


def warn(message):
    print(f"\033[33mwarning:\033[0m {message}", file=sys.stderr)


def die(message):
    print(f"\033[31merror:\033[0m {message}", file=sys.stderr)
    sys.exit(1)


def run(cmd):
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}")
    except subprocess.CalledProcessError as e:
        die(f"command failed ({e.returncode}): {' '.join(cmd)}")


def succeeds(cmd):
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def as_root(cmd):
    if os.getuid() == 0:
        run(cmd)
        return
    tool = next((t for t in ("sudo", "doas") if shutil.which(t)), None)
    if not tool:
        die(f"need root (via sudo or doas) for: {' '.join(cmd)}")
    run([tool] + cmd)


def make_venv(python):
    check = f"import sys; sys.exit(sys.version_info < {MIN_PYTHON!r})"
    if not succeeds([python, "-c", check]):
        wanted = ".".join(map(str, MIN_PYTHON))
        die(f"Python >= {wanted} required, {python} is older")
    log(f"Creating {VENV} with {python}")
    run([python, "-m", "venv", VENV])
    pip = [VENV_PYTHON, "-m", "pip", "install", "--quiet"]
    run(pip + ["--upgrade", "pip"])
    run(pip + ["-r", REQUIREMENTS])


def native_tools_present():
    result = subprocess.run([VENV_PYTHON, "-c", NATIVE_TOOLS_CHECK],
                            capture_output=True, text=True)
    if result.returncode not in (0, 3):
        die(f"cannot load the tools package:\n{result.stderr.strip()}")
    return result.returncode == 0


def build_native_tools():
    if native_tools_present():
        return
    log("Building mke2fs.android and e2fsdroid from source")
    run([VENV_PYTHON, os.path.join(ROOT, "tools", "build_android_tools.py")])


def copy_with_progress(response, f, width=30):
    total = int(response.headers.get("Content-Length") or 0)
    show = total and sys.stdout.isatty()
    done = shown = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        f.write(chunk)
        done += len(chunk)
        percent = done * 100 // total if show else 0
        if percent != shown:
            shown = percent
            filled = width * done // total
            bar = "#" * filled + "-" * (width - filled)
            print(f"\r  [{bar}] {percent:3d}%", end="", flush=True)
    if show:
        print()


def download(url, path):
    partial = path + ".part"
    try:
        with urllib.request.urlopen(url, timeout=30) as response, \
                open(partial, "wb") as f:
            copy_with_progress(response, f)
    except OSError as e:
        if os.path.exists(partial):
            os.remove(partial)
        die(f"failed to download {url}: {e}")
    os.replace(partial, path)


# Arch has apktool only in the AUR, which pacman can't install, and
# Debian's is stuck at 2.7, so fetch the upstream release jar instead.
def install_apktool():
    if shutil.which("apktool"):
        return
    bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
    log(f"Installing apktool into {bin_dir}")
    try:
        with urllib.request.urlopen(APKTOOL_RELEASE, timeout=30) as response:
            assets = json.load(response)["assets"]
    except (OSError, ValueError, KeyError) as e:
        die(f"cannot query the latest apktool release: {e}")
    url = next((a["browser_download_url"] for a in assets
                if a["name"].startswith("apktool_")
                and a["name"].endswith(".jar")), None)
    if not url:
        die("the latest apktool release has no jar")

    os.makedirs(bin_dir, exist_ok=True)
    jar = os.path.join(bin_dir, "apktool.jar")
    download(url, jar)
    wrapper = os.path.join(bin_dir, "apktool")
    with open(wrapper, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'#!/bin/sh\nexec java -jar "{jar}" "$@"\n')
    mode = os.stat(wrapper).st_mode
    os.chmod(wrapper, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        warn(f"{bin_dir} is not on PATH; add it to your shell profile "
             "so builds can find apktool")


def aur_helper():
    # AUR helpers refuse to run as root.
    if os.getuid() == 0:
        return None
    return next((h for h in ("yay", "paru") if shutil.which(h)), None)


def pacman_satisfied(dependency):
    return succeeds(["pacman", "-T", dependency])


def install_apktool_from_aur():
    helper = aur_helper()
    # The AUR package wants a full java-runtime; pulling one in on top of
    # a headless JRE would make the helper offer to replace it.
    if (shutil.which("apktool") or not helper
            or not pacman_satisfied("java-runtime")):
        return
    log(f"Installing apktool from the AUR with {helper}")
    if subprocess.run([helper, "-S", "--needed", AUR_APKTOOL]).returncode:
        warn(f"{helper} failed to install {AUR_APKTOOL}; "
             "falling back to the upstream jar")


def setup_macos():
    if not shutil.which("brew"):
        die("install Homebrew first: https://brew.sh")
    if not succeeds(["xcode-select", "-p"]):
        die("install the Command Line Tools first: xcode-select --install")

    log("Installing Homebrew packages")
    run(["brew", "install"] + BREW_PACKAGES)

    prefix = subprocess.run(["brew", "--prefix", "python@3.13"],
                            capture_output=True, text=True, check=True)
    make_venv(os.path.join(prefix.stdout.strip(), "bin", "python3.13"))
    build_native_tools()


def setup_debian():
    log("Installing apt packages")
    as_root(["apt-get", "update"])
    as_root(["apt-get", "install", "-y"] + APT_PACKAGES)

    make_venv("python3")
    install_apktool()
    build_native_tools()


def setup_arch():
    log("Installing pacman packages")
    # -Syu: Arch doesn't support installing against a stale package database.
    packages = list(PACMAN_PACKAGES)
    # The full and headless JREs conflict, so never swap an installed one.
    if not pacman_satisfied("java-runtime-headless"):
        packages.append("jre-openjdk" if aur_helper()
                        else "jre-openjdk-headless")
    as_root(["pacman", "-Syu", "--needed"] + packages)

    make_venv("python")
    install_apktool_from_aur()
    install_apktool()
    if not native_tools_present():
        die("android-tools did not provide mke2fs.android and e2fsdroid")


def setup_nixos():
    if not shutil.which("nix"):
        die("nix not found")
    log("Building the nix dev shell")
    run(["nix", "--extra-experimental-features", "nix-command flakes",
         "develop", ROOT, "--command", "python3", "-c", "import tools"])
    log("Done. Enter the environment with: nix develop")
    log("Then run builds with: python3 cli.py build <name> <firmware>")


# fsck.erofs gained --extract in erofs-utils 1.5 (Ubuntu 22.04 ships 1.4).
def check_erofs():
    has_extract = False
    if shutil.which("fsck.erofs"):
        result = subprocess.run(["fsck.erofs", "--help"], text=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        has_extract = "--extract" in result.stdout
    if not has_extract:
        warn("fsck.erofs with --extract (erofs-utils >= 1.5) not found; "
             "EROFS partitions can't be unpacked")


def detect_linux():
    fields = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    fields[key] = value.strip("\"'")
    except OSError:
        die("cannot identify this Linux distribution")

    ids = [fields.get("ID", "")] + fields.get("ID_LIKE", "").split()
    for distro, aliases in (("nixos", {"nixos"}), ("arch", {"arch"}),
                            ("debian", {"debian", "ubuntu"})):
        if aliases.intersection(ids):
            return distro
    die(f"unsupported distribution '{fields.get('ID') or 'unknown'}'; "
        "see README.md")


def main():
    global REQUIREMENTS
    for arg in sys.argv[1:]:
        if arg == "--dev":
            REQUIREMENTS = os.path.join(ROOT, "requirements-dev.txt")
        elif arg in ("-h", "--help"):
            print(__doc__.strip())
            return
        else:
            die(f"unknown option: {arg}")

    os.chdir(ROOT)
    system = platform.system()
    if system == "Darwin":
        setup_macos()
    elif system == "Linux":
        distro = detect_linux()
        if distro == "nixos":
            setup_nixos()
            return
        {"debian": setup_debian, "arch": setup_arch}[distro]()
    else:
        die(f"unsupported OS: {system}")

    log("Checking the environment")
    run([VENV_PYTHON, "-c", "import tools; tools.check_environment()"])
    check_erofs()
    log("Done. Build a GSI with: "
        ".venv/bin/python cli.py build <name> <firmware>")


if __name__ == "__main__":
    main()
