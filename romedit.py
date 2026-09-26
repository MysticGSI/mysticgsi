"""ROM tree sizes, removal history and patch configuration editing."""

from contextlib import contextmanager
import glob
import json
import os
from pathlib import Path
import re
import tempfile

import buildlock
import fsops
import make
from tools.config import DEFAULT_PARTITIONS


@contextmanager
def edit_lock(root):
    def busy():
        raise RuntimeError("Another build or edit is running")

    with buildlock.hold(on_busy=busy, path=root / buildlock.LOCK_PATH):
        yield


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=4)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    debloat = config.get("debloat", {})
    if not isinstance(debloat, dict):
        raise ValueError("debloat must be an object")
    for partition, folders in debloat.items():
        if partition not in DEFAULT_PARTITIONS:
            raise ValueError(f"Unknown partition: {partition}")
        if not isinstance(folders, dict):
            raise ValueError(f"{partition} must contain folders")
        for folder, patterns in folders.items():
            validate_relative(folder)
            if not isinstance(patterns, list):
                raise ValueError(f"{partition}/{folder} must be a list")
            for pattern in patterns:
                validate_relative(pattern)
    return config


def validate_relative(value):
    if (not isinstance(value, str) or not value or "\x00" in value
            or Path(value).is_absolute() or ".." in Path(value).parts):
        raise ValueError(f"Invalid relative path: {value!r}")


class RomEditor:
    def __init__(self, root, entry):
        self.root = Path(root)
        self.name = make.safe_name(entry["rom_name"], "build name")
        self.tree = self.root / "tmp" / self.name / "images/system"
        self.history_path = self.root / "tmp" / self.name / "removed.json"
        self.rom_type = make.safe_name(entry.get("rom_type") or "auto",
                                       "ROM type")

    def checked_path(self, relative, allow_root=False):
        validate_relative(relative)
        path = self.tree / relative
        if not allow_root and path == self.tree:
            raise ValueError("Cannot delete the ROM tree root")
        if self.tree.resolve() != self.tree.absolute():
            raise ValueError("ROM tree must not pass through a symlink")
        parent = path if allow_root else path.parent
        if not parent.resolve().is_relative_to(self.tree.resolve()):
            raise ValueError("Path leaves the ROM tree")
        return path

    def scan(self):
        if not self.tree.is_dir():
            raise ValueError("No extracted system tree; build this ROM first")
        self.checked_path(".", allow_root=True)
        sizes = {}
        stack = [(self.tree, False)]
        children = {}
        while stack:
            path, visited = stack.pop()
            if visited:
                sizes[path.relative_to(self.tree).as_posix()] = sum(
                    sizes[child.relative_to(self.tree).as_posix()]
                    for child in children[path])
                continue
            stat = path.lstat()
            if path.is_symlink() or not path.is_dir():
                sizes[path.relative_to(self.tree).as_posix()] = stat.st_size
            else:
                with os.scandir(path) as entries:
                    children[path] = sorted(Path(e.path) for e in entries)
                stack.append((path, True))
                stack.extend((child, False) for child in children[path])
        return sizes

    def config_path(self):
        prop = make.SettingsProp()
        candidates = ("build.prop", "system/build.prop",
                      "etc/build.prop", "system/etc/build.prop")
        for relative in candidates:
            path = self.checked_path(relative)
            if path.is_file() and not path.is_symlink():
                prop.init_from_file(str(path))
                break
        else:
            raise ValueError("Cannot find system build.prop")
        version = str(prop.get_android_version())
        key = (str(prop.get_sdk_version()) if re.fullmatch(
            r"\d+(?:\.\d+)*", version) else
            make.safe_name(version, "Android codename"))
        path = self.root / "patches" / key / self.rom_type / "config.json"
        patches = (self.root / "patches").resolve()
        if not path.resolve().is_relative_to(patches):
            raise ValueError("Configuration path leaves patches/")
        return path

    def read_config(self):
        path = self.config_path()
        if not path.exists():
            return {}
        return validate_config(json.loads(path.read_text()))

    def save_config(self, config):
        validate_config(config)
        with edit_lock(self.root):
            write_json(self.config_path(), config)

    def history(self):
        if not self.history_path.exists():
            return []
        paths = json.loads(self.history_path.read_text())
        if not isinstance(paths, list):
            raise ValueError("Removal history must be a list")
        for path in paths:
            validate_relative(path)
        return paths

    def debloat_rule(self, relative):
        validate_relative(relative)
        parts = list(Path(relative).parts)
        system_root = self.tree / "system"
        if (parts and parts[0] == "system" and system_root.is_dir()
                and not (self.tree / "build.prop").exists()):
            parts.pop(0)
        partition = "system"
        if parts and parts[0] in DEFAULT_PARTITIONS and parts[0] != "system":
            partition = parts.pop(0)
        if not parts:
            raise ValueError("Cannot add a partition root to debloat")
        return (partition, glob.escape(str(Path(*parts[:-1])))
                if len(parts) > 1 else ".", glob.escape(parts[-1]))

    def add_rule(self, relative):
        partition, folder, name = self.debloat_rule(relative)
        config = self.read_config()
        patterns = config.setdefault("debloat", {}).setdefault(
            partition, {}).setdefault(folder, [])
        if name not in patterns:
            patterns.append(name)
        write_json(self.config_path(), config)

    def remember(self, relative):
        with edit_lock(self.root):
            self.add_rule(relative)

    def remove(self, relative, remember=False):
        with edit_lock(self.root):
            path = self.checked_path(relative)
            if remember:
                self.add_rule(relative)
            history = self.history()
            fsops.rmrf(glob.escape(str(path)))
            if os.path.lexists(path):
                raise OSError(f"Could not completely delete {relative}")
            if relative not in history:
                history.append(relative)
            write_json(self.history_path, history)
