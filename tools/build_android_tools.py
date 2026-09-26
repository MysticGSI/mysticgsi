#!/usr/bin/env python3
"""
Builds mke2fs.android and e2fsdroid from nmeum/android-tools into tools/bin,
for hosts with no packaged copy (macOS, Debian/Ubuntu).
setup_host.py installs the build dependencies.
"""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import buildlock  # noqa: E402

VERSION = "37.0.0"
SHA256 = "2725d09f892a3a38e534429f47a321f58ecf6a3169caa42c915fb2cb7d46be0e"
URL = ("https://github.com/nmeum/android-tools/releases/download/"
       f"{VERSION}/android-tools-{VERSION}.tar.xz")
TARGETS = ("e2fsdroid", "mke2fs.android")
DESTINATION = ROOT / "tools" / "bin"
REQUIRED_COMMANDS = ("cmake", "ninja", "pkg-config")


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected source layout: {path}")
    path.write_text(text.replace(old, new, 1))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(cache: Path) -> Path:
    archive = cache / f"android-tools-{VERSION}.tar.xz"
    if not archive.exists():
        partial = archive.with_suffix(".download")
        try:
            print(f"Downloading Android tools {VERSION}..", flush=True)
            with urllib.request.urlopen(URL, timeout=60) as src, \
                    partial.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    if sha256(archive) != SHA256:
        raise RuntimeError(f"Checksum mismatch: remove {archive} and retry")
    return archive


def patch_for_darwin(vendor: Path):
    replace_once(vendor / "CMakeLists.mke2fs.txt",
                 "if(NOT APPLE)\nadd_executable(e2fsdroid",
                 "if(TRUE)\nadd_executable(e2fsdroid")
    replace_once(vendor / "CMakeLists.adb.txt",
                 "if (NOT APPLE AND NOT WIN32)", "if (NOT WIN32)")
    # Upstream omits e2fsdroid/fs_config on Darwin because the SDK has no
    # linux/capability.h. Supply only the on-disk metadata definitions used
    # by these tools, keeping SELinux labels and Android permissions intact.
    with (vendor / "CMakeLists.txt").open("a") as f:
        f.write(f'\ninclude_directories("{ROOT / "tools/macos/include"}")\n')


def build():
    missing = [c for c in REQUIRED_COMMANDS if not shutil.which(c)]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)}; run ./setup_host.py first")

    cache = ROOT / "tmp" / "android-tools-src"
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(cache)

    source = cache / f"android-tools-{VERSION}"
    # Always patch a fresh, checksum-verified source tree, so reruns are safe.
    if source.exists():
        shutil.rmtree(source)
    with tarfile.open(archive) as tar:
        tar.extractall(cache, filter="data")
    vendor = source / "vendor"
    with (vendor / "CMakeLists.txt").open("a") as f:
        f.write("\ninclude_directories("
                "boringssl/third_party/googletest/googletest/include)\n")

    cmake_args = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DANDROID_TOOLS_USE_BUNDLED_FMT=ON",
        "-DANDROID_TOOLS_PATCH_VENDOR=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    ]
    if sys.platform == "darwin":
        patch_for_darwin(vendor)
        prefix = subprocess.check_output(
            ["brew", "--prefix"], text=True).strip()
        cmake_args.append(f"-DCMAKE_PREFIX_PATH={prefix}")

    build_dir = cache / "build"
    subprocess.run(["cmake", "-S", str(source), "-B", str(build_dir),
                    "-G", "Ninja", *cmake_args], check=True)
    subprocess.run(["cmake", "--build", str(build_dir), "--target", *TARGETS,
                    "-j", str(min(os.cpu_count() or 2, 8))], check=True)

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        staged = DESTINATION / f"{name}.new"
        shutil.copy2(build_dir / "vendor" / name, staged)
        staged.replace(DESTINATION / name)
    print(f"Native image tools installed in {DESTINATION}")


def wait_for_lock():
    print("Waiting for the active build..", flush=True)


if __name__ == "__main__":
    os.chdir(ROOT)
    try:
        with buildlock.hold(on_busy=wait_for_lock):
            build()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))
