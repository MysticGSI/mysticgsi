#!/usr/bin/env python3

import argparse
import glob
import html
import json
import os
import re
import shutil
import sys

import requests

import buildlock
import fsops
import make
import tools
from tools.image.signing import signing_parameters

GSILIST = "tmp/gsilist.json"
TAGS = re.compile(r"<[^>]+>")
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

STATES = {
    "extract": "extracting firmware",
    "unpack": "unpacking images",
    "patch": "patching",
    "prepare": "preparing",
    "mke2fs": "making image",
    "sign": "signing image",
    "done": "done",
}


class ConsoleLogger:
    def __init__(self):
        self.progress = 0
        self.state = ""

    def _prefix(self):
        return f"[{self.progress:3d}%] {self.state:<8}"

    def add(self, message):
        for line in str(message).rstrip("\n").split("\n"):
            print(f"{self._prefix()} {line}", flush=True)

    def set_progress(self, value):
        self.progress = value

    def set_state(self, state):
        self.state = state
        print(f"{self._prefix()} == {STATES.get(state, state)}", flush=True)


def safe_filename(name, fallback="download.bin"):
    name = str(name).strip().replace("\\", "/").split("/")[-1]
    return UNSAFE.sub("_", name).lstrip(".")[:128] or fallback


def get_filename(url):
    if "?file_name" in url:
        return safe_filename(url.split("?file_name=")[1])
    try:
        headers = requests.head(url, allow_redirects=True, timeout=3).headers
        disposition = headers.get("Content-Disposition")
        if disposition:
            name = disposition.split("filename=")[1].split(";")[0]
            return safe_filename(name.replace('"', ''))
    except Exception:
        pass
    return safe_filename(os.path.basename(url))


def summarize(output):
    return html.unescape(TAGS.sub("", output.split("\nDownload: ")[0]))


def read_list():
    try:
        with open(GSILIST) as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return []


def write_list(entries):
    os.makedirs("tmp", exist_ok=True)
    with open(GSILIST, "w") as f:
        json.dump(entries, f, indent=4)


def append_list(wt: make.RomPorter):
    entries = read_list()
    entries.append({
        "rom_name": wt.rom_name,
        "variant_tag": wt.variant_tag,
        "override_rom_type": wt.override_rom_type,
        "rom_type": wt.rom_type,
        "output": wt.output,
        "output_name": wt.output_name,
        "output_path": wt.output_path,
    })
    write_list(entries)


def fetch(url, out_dir):
    if not shutil.which("aria2c"):
        print("aria2c not found on PATH -- install it, or pass a local file "
              "instead of a URL.", file=sys.stderr)
        return None

    os.makedirs(out_dir, exist_ok=True)
    name = get_filename(url)
    path = os.path.join(out_dir, name)

    rc = fsops.run(["aria2c", "-x16", "-s16", "--continue=true",
                    "--dir", out_dir, "--out", name, url])
    if rc != 0 or not os.path.exists(path):
        print(f"download failed (aria2c exited {rc})", file=sys.stderr)
        return None
    return path


def wait_for_lock():
    print("waiting for another build to finish...", flush=True)


def cmd_build(args):
    try:
        tools.check_environment()
    except (RuntimeError, OSError) as e:
        print(e, file=sys.stderr)
        return 1
    rom_type, _, rom_custom = args.type.partition(":")

    try:
        wt = make.RomPorter(args.name, args.add)
        wt.rom_type = make.safe_name(rom_type, "rom_type")
        wt.override_rom_type = make.safe_name(
            rom_custom or "default", "rom_custom")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    wt.logger = ConsoleLogger()
    wt.debloat = not args.no_debloat
    wt.avb_key = args.avb_key

    with buildlock.hold(on_busy=wait_for_lock):
        if "://" in args.source:
            os.makedirs(wt.work_dir, exist_ok=True)
            filename = fetch(args.source, wt.work_dir)
            if filename is None:
                return 1
        else:
            filename = args.source
            if not os.path.exists(filename):
                print(f"no such file: {filename}", file=sys.stderr)
                return 1

        rc = wt.build(filename)
        if rc != 0:
            print(f"build failed ({rc})", file=sys.stderr)
            return 1
        if args.compress and wt.compress_output() != 0:
            print("compression failed", file=sys.stderr)
            return 1
        append_list(wt)

    print()
    print(summarize(wt.output))
    print()
    print(f"{wt.output_path}.img")
    if args.compress:
        print(f"{wt.output_path}.zip")
    return 0


def cmd_rebuild(args):
    entries = read_list()
    entry = next((e for e in reversed(entries)
                  if e.get("rom_name") == args.name), None)
    if entry is None:
        print(f"no build named {args.name!r} in {GSILIST}", file=sys.stderr)
        return 1
    try:
        tools.check_environment()
    except (RuntimeError, OSError) as e:
        print(e, file=sys.stderr)
        return 1

    wt = make.RomPorter(entry["rom_name"], entry.get("variant_tag", ""))
    wt.avb_key = args.avb_key
    wt.logger = ConsoleLogger()
    with buildlock.hold(on_busy=wait_for_lock):
        system_size = wt.rebuild(entry["output_name"])
        if system_size is None:
            print("rebuild failed", file=sys.stderr)
            return 1
        if args.compress and wt.compress_output() != 0:
            print("compression failed", file=sys.stderr)
            return 1
        entry["output"] = make.replace_image_size(entry["output"],
                                                  system_size)
        write_list(entries)

    print(f"\n{wt.output_path}.img ({make.bytes_to_human(system_size)})")
    if args.compress:
        print(f"{wt.output_path}.zip")
    return 0


def cmd_list(args):
    entries = read_list()
    if not entries:
        print(f"no builds recorded in {GSILIST}")
        return 0

    for i, entry in enumerate(entries):
        img = f"{entry.get('output_path', '')}.img"
        mark = "" if os.path.exists(img) else "  (missing)"
        print(f"{i}: {entry.get('output_name', '?')}\n   {img}{mark}")
    return 0


def cmd_clean(args):
    with buildlock.hold(on_busy=wait_for_lock):
        targets = [e for d in ("tmp", "out")
                   for e in glob.glob(os.path.join(d, "*"))]
        if not targets:
            print("nothing to clean")
            return 0

        freed = sum(fsops.disk_usage(t) for t in targets)
        print(f"{len(targets)} entries under tmp/ and out/, {human(freed)}")
        if not args.yes:
            answer = input("remove them? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted")
                return 1

        for entry in targets:
            if os.path.isdir(entry) and not os.path.islink(entry):
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    os.remove(entry)
                except OSError:
                    pass
        print(f"freed {human(freed)}")
        return 0


def human(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


def avb_key_path(value):
    try:
        path, _ = signing_parameters(value)
    except (OSError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return path


def cmd_tui(args):
    from tui import MysticApp

    MysticApp().run()
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="CLI entry point for mysticgsi builds.")
    sub = ap.add_subparsers(dest="command", required=True)

    tui = sub.add_parser("tui", help="open the interactive terminal app")
    tui.set_defaults(func=cmd_tui)

    build = sub.add_parser(
        "build", help="build a GSI from a URL or a local file")
    build.add_argument(
        "name", help="short name for the build; names out/<name>/")
    build.add_argument(
        "source", help="firmware URL or path to a local archive")
    build.add_argument(
        "--type", default="auto", metavar="TYPE[:CUSTOM]",
        help="ROM type, optionally TYPE:CUSTOMNAME (default: auto)")
    build.add_argument(
        "--add", default="", help="tag appended to the build's display name")
    build.add_argument(
        "--compress", action="store_true", help="also produce a .zip")
    build.add_argument(
        "--no-debloat", action="store_true",
        help="keep the apps the ROM's patch set would remove")
    build.set_defaults(func=cmd_build)

    rebuild = sub.add_parser(
        "rebuild",
        help="rebuild a build's image from its (edited) tree in "
             "tmp/<name>/images/system")
    rebuild.add_argument("name", help="name of an earlier build")
    rebuild.add_argument(
        "--compress", action="store_true", help="also produce a .zip")
    for command in (build, rebuild):
        command.add_argument(
            "--avb-key", type=avb_key_path, metavar="PEM",
            help="RSA private key for AVB signing (default: AOSP test key)")
    rebuild.set_defaults(func=cmd_rebuild)

    lst = sub.add_parser("list", help=f"list builds recorded in {GSILIST}")
    lst.set_defaults(func=cmd_list)

    clean = sub.add_parser(
        "clean", help="remove everything under tmp/ and out/")
    clean.add_argument(
        "-y", "--yes", action="store_true", help="skip the confirmation")
    clean.set_defaults(func=cmd_clean)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
