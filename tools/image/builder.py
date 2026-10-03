"""
Builds the ext4 system image with mke2fs and e2fsdroid.
"""

from typing import Optional
from pathlib import Path
import json
import os
import shutil
import subprocess

import fsops

from ..config import BLOCK_SIZE
from ..host import configure_environment, find_tool
from .contexts import prepare_file_contexts

DEFAULT_TIMESTAMP = "1230768000"
MKE2FS_CONFIG = Path(__file__).resolve().with_name("mke2fs.conf").as_posix()
STUB_DIRS = ("persist", "bt_firmware", "firmware", "dsp", "cache")
FS_CONFIG_FILES = ("fs_config_files", "fs_config_dirs")
# Partitions merged into the GSI's system tree whose own fs_config tables
# still apply (libcutils maps system/<partition>/... onto <partition>/...).
MERGED_PARTITIONS = ("product", "system_ext")


def clean_stub_directories(system_dir: str):
    """Replaces the stub mountpoints in system_dir with empty directories."""
    for stub in STUB_DIRS:
        stub_path = os.path.join(system_dir, stub)
        if os.path.isdir(stub_path) and not fsops.islink(stub_path):
            shutil.rmtree(stub_path, ignore_errors=True)
        elif os.path.lexists(stub_path):
            try:
                os.remove(stub_path)
            except OSError:
                pass
        os.makedirs(stub_path, exist_ok=True)


def _fs_config_root(source_dir: str, staging_dir: str) -> str:
    """
    Lays out the ROM's fs_config tables where e2fsdroid's -p looks for them
    (<root>/<partition>/etc/fs_config_*), so the image gets the stock
    owners, modes and capabilities. Returns the path to pass as -p.
    """
    root = os.path.join(staging_dir, "fs_config")
    shutil.rmtree(root, ignore_errors=True)
    sources = {"system": os.path.join(source_dir, "system", "etc")}
    for part in MERGED_PARTITIONS:
        sources[part] = os.path.join(source_dir, "system", part, "etc")

    for part, etc in sources.items():
        for name in FS_CONFIG_FILES:
            src = os.path.join(etc, name)
            if os.path.isfile(src):
                dst_dir = os.path.join(root, part, "etc")
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copyfile(src, os.path.join(dst_dir, name))
    # libcutils strips a trailing "/system" to find the other partitions.
    return os.path.join(root, "system")


def _run_step(name, cmd, env, output_image, log) -> int:
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        log(
            f"{name} failed with exit code {res.returncode}:\n"
            f"{res.stdout}\n{res.stderr}"
        )
        try:
            os.remove(output_image)
        except OSError:
            pass
    return res.returncode


def build_system_image(
    source_dir: str,
    output_image: str,
    system_size: int,
    staging_dir: Optional[str] = None,
    file_contexts_path: Optional[str] = None,
    stock_labels_path: Optional[str] = None,
    logger=None,
) -> int:
    """
    Builds an ext4 image of system_size bytes from source_dir. SELinux
    file_contexts are generated into staging_dir unless file_contexts_path
    is given, filling the ROM rules' gaps from stock_labels_path (a JSON
    map of paths in source_dir to the labels their images had). Returns 0
    on success.
    """
    log = logger or print
    source_dir = Path(source_dir).resolve().as_posix()
    output_image = Path(output_image).resolve().as_posix()

    configure_environment()
    mke2fs_bin = find_tool("mke2fs")
    e2fsdroid_bin = find_tool("e2fsdroid")
    if not mke2fs_bin:
        log("Error: mke2fs executable not found")
        return 1
    if not e2fsdroid_bin:
        log("Error: e2fsdroid executable not found")
        return 1

    if not os.path.isdir(source_dir):
        log(f"Error: Source directory not found: {source_dir}")
        return 1

    clean_stub_directories(source_dir)

    work_dir = staging_dir or os.path.dirname(output_image) or "."
    if not file_contexts_path:
        stock_labels = None
        if stock_labels_path and os.path.isfile(stock_labels_path):
            with open(stock_labels_path, encoding="utf-8") as f:
                stock_labels = json.load(f)
        file_contexts_path = prepare_file_contexts(
            source_dir, os.path.join(work_dir, "file_contexts"), stock_labels
        )

    blocks = system_size // BLOCK_SIZE
    if blocks <= 0:
        log(f"Error: Invalid system image size: {system_size}")
        return 1

    out_dir = os.path.dirname(output_image)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_image, "wb"):
        pass

    env = os.environ.copy()
    env["E2FSPROGS_FAKE_TIME"] = DEFAULT_TIMESTAMP

    log(f"Formatting ext4 filesystem ({blocks} blocks of {BLOCK_SIZE} bytes)")
    mke2fs_cmd = [
        mke2fs_bin,
        "-O",
        "^has_journal",
        "-L",
        "/",
        "-I",
        "256",
        "-M",
        "/",
        "-m",
        "0",
        "-t",
        "ext4",
        "-b",
        str(BLOCK_SIZE),
        output_image,
        str(blocks),
    ]
    mke2fs_env = dict(env, MKE2FS_CONFIG=MKE2FS_CONFIG)
    rc = _run_step("mke2fs", mke2fs_cmd, mke2fs_env, output_image, log)
    if rc != 0:
        return rc

    log("Populating filesystem with e2fsdroid")
    # -p adds the ROM's own fs_config tables (vendor uids, modes and
    # capabilities) on top of the AOSP defaults e2fsdroid applies anyway.
    e2fsdroid_cmd = [
        e2fsdroid_bin,
        "-e",
        "-T",
        DEFAULT_TIMESTAMP,
        "-p",
        Path(_fs_config_root(source_dir, work_dir)).resolve().as_posix(),
    ]
    if file_contexts_path and os.path.isfile(file_contexts_path):
        e2fsdroid_cmd += ["-S", Path(file_contexts_path).resolve().as_posix()]
    e2fsdroid_cmd += ["-f", source_dir, "-a", "/", output_image]
    rc = _run_step("e2fsdroid", e2fsdroid_cmd, env, output_image, log)
    if rc != 0:
        return rc

    if not os.path.isfile(output_image) or os.path.getsize(output_image) == 0:
        log("Error: Output image is missing or empty after e2fsdroid")
        return 1

    size_mb = os.path.getsize(output_image) // (1024 * 1024)
    log(f"System image created: {output_image} ({size_mb} MB)")
    return 0
