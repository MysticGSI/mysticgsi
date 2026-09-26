#!/usr/bin/env python3
import os.path
import stat
import subprocess
import sys
from platform import system
from shutil import which
from venv import main as venv_main

import requests



ROOT = str(os.path.dirname(__file__))
VENV = f"{ROOT}/.venv"
REQUIREMENTS = f"{ROOT}/requirements.txt"
MIN_PYTHON = "3.10"
help_info = """
Installs MysticGSI's dependencies on macOS, Debian/Ubuntu, Arch and NixOS.

   ./setup.py          runtime dependencies
   ./setup.py --dev    plus pytest and flake8
   
Safe to re-run: package managers skip what is installed, and the native
image tools are only rebuilt when missing.
"""


def log(message: str):
    print(f"\033[1m==> {message}\033[0m\n")


def warn(message: str):
    print(f'\033[33mwarning:\033[0m {message}\n')


def die(message: str):
    print(f'\033[31merror:\033[0m {message}\n')
    sys.exit(1)


def run(cmd: list):
    return subprocess.run(cmd).returncode


def as_root(cmd: list):
    if os.getuid() == 0:
        return subprocess.run(cmd).returncode
    elif which('sudo'):
        return subprocess.run(["pkexec"] + cmd).returncode
    else:
        die(f"need root for: {cmd}")


def make_venv():
    if sys.version_info < (3, 10):
        die(f"Python >= {MIN_PYTHON} required, found {sys.version}")
    log(f"Creating {VENV} with {sys.version}")
    run(['python3', '-m', 'venv', VENV])
    run([f"{VENV}/bin/python", "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([f"{VENV}/bin/python", "-m", "pip", "install", "--quiet", "-r", REQUIREMENTS])

def native_tools_present() -> bool:
    sys.path.insert(0, ".")
    from tools.host import check_environment
    try:
        check_environment()
        return True
    except RuntimeError:
        return False

def build_native_tools():
    if not native_tools_present():
        return
    log("Building mke2fs.android and e2fsdroid from source")
    os.chdir(ROOT)
    from tools.build_android_tools import build, buildlock, wait_for_lock
    try:
        with buildlock.hold(on_busy=wait_for_lock):
            build()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        die(e)


def chmod_plus_x(file_path: str):
    current_mode = os.stat(file_path).st_mode
    new_mode = current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    os.chmod(file_path, new_mode)

def download(download_url:str, path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"{path} already exists.")
        return True
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    r = requests.get(download_url)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
    return r.status_code == 200

def install_apktool():
    if which('apktool'):
        return
    home_path = os.environ["HOME"]
    bin_path = f"{home_path}/.local/bin"
    log(f"Installing apktool into {bin_path}")
    r = requests.get('https://api.github.com/repos/iBotPeaches/Apktool/releases/latest')
    json = r.json()
    assets = json["assets"]
    download_link = next(a["browser_download_url"] for a in assets
               if a["name"].startswith("apktool_") and a["name"].endswith(".jar"))
    os.makedirs(bin_path, exist_ok=True)
    log('Downloading apktool...')
    download(download_link, f"{bin_path}/apktool.jar")
    with open(f'{bin_path}/apktool', newline='\n', encoding='utf-8', mode='w') as f:
        f.write("#!/bin/sh\n")
        f.write('exec java -jar "my_bin/apktool.jar" "$@"\n')
    chmod_plus_x(f'{bin_path}/apktool')
    if bin_path not in os.environ['PATH']:
        warn(f"{bin_path} is not on PATH; add it to your shell profile\nso builds can find apktool")


def setup_macos():
    if not which('brew'):
        die("install Homebrew first: https://brew.sh")
    if run(['xcode-select', '-p']):
        die("install the Command Line Tools first: xcode-select --install")
    log("Installing Homebrew packages")
    run(['brew', 'install', 'python@3.13', 'cmake', 'ninja', 'pkgconf', 'erofs-utils', 'brotli', 'lz4', 'pcre2', 'libusb', 'zstd', 'protobuf', 'aria2', 'apktool', 'gpatch'])
    make_venv()
    build_native_tools()

def setup_debian():
    packages = ['python3', 'python3-venv', 'python3-pip', 'erofs-utils', 'aria2', 'patch', 'default-jre-headless', 'curl', 'ca-certificates', 'build-essential', 'cmake', 'ninja-build', 'pkg-config', 'perl', 'golang-go', 'libgtest-dev', 'libusb-1.0-0-dev', 'libpcre2-dev', 'libprotobuf-dev', 'protobuf-compiler', 'libbrotli-dev', 'liblz4-dev', 'libzstd-dev']
    log("Installing apt packages")
    as_root(['apt-get', 'update'])
    as_root(['apt-get', 'install', '-y'] + packages)
    make_venv()
    install_apktool()
    build_native_tools()

def setup_arch():
    log("Installing pacman packages")
    as_root(['pacman', '-Syu', '--needed', 'python', 'python-pip', 'erofs-utils', 'aria2', 'patch', 'jre-openjdk-headless', 'android-tools', 'curl'])
    make_venv()
    install_apktool()
    if not native_tools_present():
        die("android-tools did not provide mke2fs.android and e2fsdroid")

def setup_nixos():
    if not which('nix'):
        die("nix not found")
    log("Building the nix dev shell")
    run(['nix', '--extra-experimental-features', "nix-command flakes", 'develop', ROOT, '--command', 'python3', '-c', 'import tools'])
    log("Done. Enter the environment with: nix develop")
    log("Then run builds with: python3 cli.py build <name> <firmware>")

def check_erofs():
    if not which('fsck.erofs'):
        warn("fsck.erofs with --extract (erofs-utils >= 1.5) not found;" 
            "EROFS partitions can't be unpacked")
def detect_linux():
    dist = ''
    if not os.path.exists('/etc/os-release'):
        die("cannot identify this Linux distribution")
    with open('/etc/os-release', 'r') as f:
        for i in f.readlines():
            if i.startswith('ID=') or i.startswith('ID_LIKE='):
                dist = i.split('=')[1]
                break
    if 'nixos' in dist:
        return 'nixos'
    if "arch" in dist:
        return 'arch'
    if 'debian' in dist:
        return 'debian'
    die(f"unsupported distribution {dist or 'unknown'}; see README.md")

def main():
    argv = sys.argv[1] if sys.argv.__len__() >= 2 else ""
    if argv in ['--help', '-h']:
        print(help_info)
        return 0
    if argv == '--dev':
        global REQUIREMENTS
        REQUIREMENTS = f"{ROOT}/requirements-dev.txt"
    if argv:
        die(f"Unknown Option:{argv}")
    if system() == 'Darwin':
        setup_macos()
    if system() == 'Linux':
        dist = detect_linux()
        if dist == 'debian':
            setup_debian()
        if dist == 'arch':
            setup_arch()
        if dist == 'nixos':
            setup_nixos()
            return
    else:
        die(f'Unsupported system :{system()}')
    log("Checking the environment")
    from tools.host.env import check_environment
    check_environment()
    check_erofs()
    log('Done. Build a GSI with: .venv/bin/python cli.py build <name> <firmware>"')

if __name__ == '__main__':
    main()
