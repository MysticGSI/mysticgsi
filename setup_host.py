#!/usr/bin/env python3

from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)


BREW_PACKAGES = [
    "python@3.13",
    "curl",
    "aria2",
    "gpatch",
    "openssl@3",
]

APT_PACKAGES = [
    "python3",
    "python3-venv",
    "python3-pip",
    "curl",
    "aria2",
    "openssl",
    "patch",
    "ca-certificates",
    "libarchive-tools",
]

PACMAN_PACKAGES = [
    "python",
    "python-pip",
    "curl",
    "aria2",
    "openssl",
    "patch",
    "libarchive",
]


def setup_macos(dev):
    if not shutil.which("brew"):
        raise RuntimeError("install Homebrew first: https://brew.sh")

    print("Installing Homebrew packages")
    subprocess.run(["brew", "install", *BREW_PACKAGES], check=True)
    if not shutil.which("java"):
        print("Java not found. Installing openjdk@17")
        subprocess.run(["brew", "install", "openjdk@17"], check=True)

    prefix = subprocess.run(
        ["brew", "--prefix", "python@3.13"], capture_output=True, text=True, check=True
    )
    make_venv(Path(prefix.stdout.strip()) / "bin/python3.13", dev)


def setup_debian(dev):
    if not shutil.which("sudo"):
        raise RuntimeError("sudo is required to install Debian/Ubuntu packages")

    print("Installing apt packages")
    subprocess.run(["sudo", "apt-get", "update"], check=True)
    subprocess.run(["sudo", "apt-get", "install", "-y", *APT_PACKAGES], check=True)
    if not shutil.which("java"):
        print("Java not found. Installing default-jre-headless")
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "default-jre-headless"], check=True
        )
    make_venv("python3", dev)


def setup_arch(dev):
    if not shutil.which("sudo"):
        raise RuntimeError("sudo is required to install Arch packages")

    print("Installing pacman packages")
    subprocess.run(["sudo", "pacman", "-Syu", "--needed", *PACMAN_PACKAGES], check=True)
    if not shutil.which("java"):
        print("Java not found. Installing jre17-openjdk-headless")
        subprocess.run(
            ["sudo", "pacman", "-S", "--needed", "jre17-openjdk-headless"],
            check=True,
        )
    make_venv("python", dev)


def setup_nixos():
    print("Building the Nix dev shell")
    subprocess.run(
        [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "develop",
            str(ROOT),
            "--command",
            "python3",
            "-c",
            "import tools",
        ],
        cwd=ROOT,
        check=True,
    )
    print("Done. Enter the environment with: nix develop")
    print("Then run builds with: python3 cli.py build <name> <firmware>")


def check_distro():
    release = {}
    with open("/etc/os-release", encoding="utf-8") as source:
        for line in source:
            key, separator, value = line.strip().partition("=")
            if separator:
                release[key] = value.strip('"\'')

    distros = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    if distros & {"debian", "ubuntu"}:
        return "debian"
    if "arch" in distros:
        return "arch"
    if "nixos" in distros:
        return "nixos"
    raise RuntimeError("Unsupported Linux distro")


def make_venv(python, dev):
    version = subprocess.run(
        [str(python), "-c", "import sys; print(*sys.version_info[:2])"],
        capture_output=True,
        text=True,
        check=True,
    )
    if tuple(map(int, version.stdout.split())) < MIN_PYTHON:
        raise RuntimeError("Python 3.10 or newer is required")

    venv = ROOT / ".venv"
    venv_python = venv / "bin/python"
    requirements = ROOT / ("requirements-dev.txt" if dev else "requirements.txt")

    subprocess.run([str(python), "-m", "venv", str(venv)], check=True)
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
    )
    print(f"Done. Build a GSI with: {venv_python} cli.py build <name> <firmware>")


def main():
    if sys.argv[1:] not in ([], ["--dev"]):
        raise RuntimeError("Usage: setup_host.py [--dev]")
    dev = "--dev" in sys.argv[1:]

    system = platform.system()
    if system == "Darwin":
        setup_macos(dev)
    elif system == "Linux":
        distro = check_distro()
        if distro == "debian":
            setup_debian(dev)
        elif distro == "arch":
            setup_arch(dev)
        elif distro == "nixos":
            setup_nixos()
    else:
        raise RuntimeError(f"Unsupported OS: {system}")


if __name__ == "__main__":
    main()
