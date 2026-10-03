import datetime
import json
import os
import re
import tempfile
import traceback
import zipfile
from typing import ClassVar

import fsops
import tools
from tools.config import DEFAULT_PARTITIONS
from tools.isa import find_cpu_features
from tools.porting import naming
from tools.porting.context import PatchContext
from tools.porting.pipeline import run as run_patches
from tools.porting.properties import SettingsProp

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_name(value: object, fallback: str = "unknown") -> str:
    value = UNSAFE_CHARS.sub("_", str(value)).strip("._-")
    return value[:96] or fallback


def safe_name(value: object, what: str) -> str:
    value = str(value)
    if not SAFE_NAME.match(value) or value in (".", ".."):
        raise ValueError(
            f"invalid {what}: {value!r} -- allowed: letters, digits, dot, "
            f"dash, underscore (max 64 chars)"
        )
    return value


SIZE_UNITS = ["Bytes", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"]


def bytes_to_human(b: int) -> str:
    d = ""
    s = 0

    while b > 1024:
        d = f".{b % 1024 * 100 // 1024:02d}"
        b //= 1024
        s += 1

    return f"{b}{d} {SIZE_UNITS[s]}"


def replace_image_size(text: str, system_size: int) -> str:
    """Updates the "Raw Image Size:" line of a build summary."""
    return re.sub(
        r"Raw Image Size: [^\n<]*",
        f"Raw Image Size: {bytes_to_human(system_size)}",
        text,
    )


patches_dir = "patches"
tmp_dir = "tmp"
output_dir = "out"


class StubLogger:
    def set_progress(self, progress: int) -> None: ...

    def set_state(self, state: str) -> None: ...

    def add(self, message: str) -> None:
        print(message)


class RomPorter:
    PARTITION_NAMES: ClassVar[list[str]] = DEFAULT_PARTITIONS

    def __init__(self, rom_name: str, variant_tag: str = "") -> None:
        self.images_dir = ""
        self.logger = StubLogger()
        self.patch_context = PatchContext(
            partition_dirs={},
            image_files={},
            log=self.log,
            patches_dir=patches_dir,
        )
        # Partition -> {path in its image: SELinux label}.
        self.stock_labels: dict[str, dict[str, str]] = {}
        self.rom_name = safe_name(rom_name, "rom_name")
        self.rom_type = "auto"
        self.variant_tag = variant_tag
        self.override_rom_type = "default"
        self.is_64bit_only = False
        self.programs_32bit_only = []
        self.debloat = True
        self.avb_key = None
        self.work_dir = f"{tmp_dir}/{rom_name}"
        self.cpu_warning = ""

    @property
    def partition_dirs(self) -> dict[str, str]:
        return self.patch_context.partition_dirs

    @partition_dirs.setter
    def partition_dirs(self, value: dict[str, str]) -> None:
        self.patch_context.partition_dirs = value

    @property
    def image_files(self) -> dict[str, str]:
        return self.patch_context.image_files

    @image_files.setter
    def image_files(self, value: dict[str, str]) -> None:
        self.patch_context.image_files = value

    @property
    def props(self) -> dict[str, SettingsProp]:
        return self.patch_context.props

    @props.setter
    def props(self, value: dict[str, SettingsProp]) -> None:
        self.patch_context.props = value

    def log(self, message: str) -> None:
        self.logger.add(message)

    def build(self, filename: str) -> int:
        if not os.path.exists(tmp_dir):
            os.mkdir(tmp_dir)
        if not os.path.exists(self.work_dir):
            os.mkdir(self.work_dir)

        self.logger.set_progress(10)
        self.logger.set_state("extract")

        res = self._extract_firmware(os.path.abspath(filename).split("?")[0])
        if res != 0:
            self.log("Extracting firmware failed. Check logs.")
            return -1

        self.logger.set_progress(30)
        self.logger.set_state("unpack")

        res = self._unpack_partitions()
        if res != 0:
            self.log("Unpacking images failed. Check logs.")
            return -1

        self.logger.set_progress(50)
        self.logger.set_state("patch")

        res = self.patch()
        if res != 0:
            self.log("Patching failed. Check logs.")
            return -1

        self.logger.set_progress(70)
        self.logger.set_state("prepare")

        res = self.prepare()
        if res != 0:
            self.log("Preparing failed. Check logs")
            return -1

        self.logger.set_progress(90)
        self.logger.set_state("mke2fs")

        res = self._create_system_image()
        if res != 0:
            self.log("Making image failed. Check logs.")
            return -1

        arch = "64-bit only" if self.is_64bit_only else "32/64-bit"
        self.output = (
            f"<b>{self.get_display_name()} ({arch})\n"
            f"Ported from {self.device_model} ({self.device_codename})\n\n"
            f"Info</b>: <pre>{self.build_info_text}</pre>"
        )

        self.logger.set_progress(100)
        self.logger.set_state("done")
        self.log("Done!")

        return 0

    def _unpack_image(self, name: str, path: str, out: str) -> int:
        fs = tools.detect_filesystem(path)
        self.log(f"Unpacking {name} ({fs})")
        rc = tools.unpack_filesystem(path, out, fs_type=fs, logger=self.log)
        if rc != 0:
            return -1
        labels = tools.read_labels(path, fs, logger=self.log)
        if labels is not None:
            self.stock_labels[name] = labels
        return 0

    def _unpack_partitions(self) -> int:
        self.partition_dirs.clear()
        self.stock_labels.clear()
        if "system" not in self.image_files:
            self.log("Firmware contains no system image")
            return -1
        for name, path in self.image_files.items():
            out = os.path.join(self.images_dir, name)
            if self._unpack_image(name, path, out) != 0:
                return -1
            self.partition_dirs[name] = out
        return 0

    def _extract_firmware(self, archive_path: str) -> int:
        self.images_dir = os.path.join(self.work_dir, "images")
        fsops.rmrf(f"{self.images_dir}")
        os.mkdir(self.images_dir)

        self.image_files.clear()
        rc = tools.extract_firmware(
            archive_path=archive_path,
            output_dir=self.images_dir,
            target_partitions=RomPorter.PARTITION_NAMES,
            logger=self.log,
        )
        if rc != 0:
            self.log(f"Firmware extraction failed ({rc})")
            return -1

        for _, _, files in os.walk(self.images_dir):
            for f in files:
                partition = f.split(".")[0]
                if partition in RomPorter.PARTITION_NAMES:
                    self.image_files[partition] = os.path.join(
                        self.images_dir, f
                    )

        if "system" not in self.image_files:
            return -1

        return 0

    def get_rom_name(self) -> str:
        self.patch_context.rom_type = self.rom_type
        return naming.get_rom_name(self.patch_context)

    def get_display_name(self) -> str:
        self.patch_context.rom_type = self.rom_type
        return naming.get_display_name(self.patch_context, self.variant_tag)

    def patch(self) -> int:
        ctx = self.patch_context
        ctx.rom_type = self.rom_type
        ctx.override_rom_type = self.override_rom_type
        ctx.debloat = self.debloat
        ctx.patches_dir = patches_dir
        try:
            ctx.resolve_partitions()
            ctx.capture_info()
            run_patches(ctx)
            self.rom_type = ctx.rom_type
            self.device_model = ctx.info.device_model
            self.device_codename = ctx.info.device_codename
            self.android_version = ctx.info.android_version
            self.build_incremental = ctx.info.build_incremental
            self.is_64bit_only = ctx.is_64bit_only
            self.programs_32bit_only = ctx.programs_32bit_only
            self.build_info_text = ctx.build_summary()
        except Exception as e:
            traceback.print_exc()
            self.log(f"Patching failed: {type(e).__name__}: {e}")
            return -1
        return 0

    def _get_system_root(self) -> str:
        return self.patch_context.system_root()

    def prepare(self) -> int:
        self.log("Merging dynamic partitions..")
        system_dir = self.partition_dirs["system"]
        # Partition -> where its root ended up in the system tree.
        placements = {}

        for i in self.image_files:
            if i in ("system", "vendor", "odm"):
                continue
            if i == "mi_ext" and self.rom_type != "hyperos":
                continue

            try:
                if self.partition_dirs[i] in (
                    f"{system_dir}/system/{i}",
                    f"{system_dir}/{i}",
                ):
                    continue

                if self.rom_type not in (
                    "miui",
                    "hyperos",
                    "joyui",
                    "itel",
                    "nothing",
                ) and i in ("system_ext", "product"):
                    fsops.rmrf(f"{system_dir}/system/{i}")
                    fsops.rmrf(f"{system_dir}/{i}")
                    fsops.cp_r(
                        f"{self.partition_dirs[i]}",
                        f"{system_dir}/system/{i}/",
                    )
                    fsops.symlink(f"/system/{i}", f"{system_dir}/{i}")
                    placements[i] = f"/system/{i}"
                else:
                    fsops.rmrf(f"{system_dir}/{i}")
                    fsops.cp_r(
                        f"{self.partition_dirs[i]}", f"{system_dir}/{i}"
                    )
                    placements[i] = f"/{i}"
            except Exception:
                traceback.print_exc()

                return -1

        self._save_stock_labels(placements)
        return 0

    def _save_stock_labels(self, placements: dict[str, str]) -> None:
        """
        Writes the stock labels of everything merged into the system tree,
        keyed by final path, for the image stage (and later rebuilds).
        """
        labels = dict(self.stock_labels.get("system", {}))
        for part, prefix in placements.items():
            for path, label in self.stock_labels.get(part, {}).items():
                labels[prefix if path == "/" else prefix + path] = label
        with open(
            os.path.join(self.images_dir, "stock_labels.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(labels, f)

    def _warn_cpu_features(self) -> None:
        self.cpu_warning = ""
        system = self._get_system_root()
        found = {}
        for relative_path in (
            "bin/init",
            "bin/bootstrap/linker64",
            "bin/linker64",
            "bin/app_process64",
            "bin/servicemanager",
            "bin/hwservicemanager",
            "bin/surfaceflinger",
        ):
            path = os.path.join(system, relative_path)
            if not os.path.lexists(path) or os.path.islink(path):
                continue
            features = find_cpu_features(path)
            if features is None:
                self.log(
                    f"Warning: could not check CPU instructions in "
                    f"system/{relative_path}"
                )
                continue
            for feature in sorted(features):
                found.setdefault(feature, f"system/{relative_path}")
        if found:
            evidence = ", ".join(
                f"{feature} ({path})"
                for feature, path in sorted(found.items())
            )
            self.cpu_warning = (
                f"Newer ARM instructions found: {evidence}. Devices lacking "
                "these features may fail to boot; runtime CPU checks may "
                "provide fallbacks."
            )
            self.log("Warning: " + self.cpu_warning)

    def _write_image(self, output_name: str) -> int | None:
        """
        Builds out/<rom_name>/<output_name>.img from the system tree, sized
        to fit its contents. Returns the signed image size, or None.
        """
        self._warn_cpu_features()
        system_dir = self.partition_dirs["system"]
        out_dir = os.path.join(output_dir, self.rom_name)
        # Allocated blocks, not file sizes: small files and directories
        # each take at least a block in the image.
        system_size = int(
            fsops.disk_usage(system_dir) * 1.05 + 32 * 1024 * 1024
        )
        os.makedirs(out_dir, exist_ok=True)

        self.log(f"Making image {output_name}..")
        with tempfile.TemporaryDirectory(
            prefix="image-", dir=out_dir
        ) as staging:
            image = os.path.join(staging, "system.img")
            rc = tools.build_system_image(
                source_dir=system_dir,
                output_image=image,
                system_size=system_size,
                staging_dir=self.images_dir,
                stock_labels_path=os.path.join(
                    self.images_dir, "stock_labels.json"
                ),
                logger=self.log,
            )
            if (
                rc != 0
                or not os.path.isfile(image)
                or os.path.getsize(image) == 0
            ):
                self.log(f"Image builder failed ({rc})")
                return None
            self.logger.set_state("sign")
            if (
                tools.sign_system_image(
                    image, logger=self.log, key_path=self.avb_key
                )
                != 0
            ):
                return None
            system_size = os.path.getsize(image)
            os.replace(image, f"{out_dir}/{output_name}.img")

        self.output_name = output_name
        self.output_path = f"{out_dir}/{output_name}"
        return system_size

    def _create_system_image(self) -> int:
        date = datetime.datetime.now().strftime("%Y%m%d")
        try:
            self.rom_type.capitalize()
        except Exception:
            self.rom_type = "generic"
        output_name = sanitize_name(
            f"{self.get_rom_name()}-{self.device_codename}"
            f"-{self.android_version}-{self.build_incremental}"
            f"-AB-{date}-MysticGSI"
        )

        try:
            system_size = self._write_image(output_name)
            if system_size is None:
                return -1
            if self.cpu_warning:
                self.build_info_text += (
                    f"CPU compatibility: {self.cpu_warning}\n"
                )
            self.build_info_text += (
                f"Raw Image Size: {bytes_to_human(system_size)}\n"
            )
            with open(
                os.path.join(output_dir, self.rom_name, "output.txt"), "w"
            ) as f:
                f.write(self.build_info_text)

        except Exception:
            traceback.print_exc()
            return -1

        return 0

    def rebuild(self, output_name: str) -> int | None:
        """
        Rebuilds the image from the system tree a previous build left in
        tmp/<rom_name>/images/system, e.g. after debloating it by hand.
        Returns the new raw image size in bytes, or None.
        """
        self.images_dir = os.path.join(self.work_dir, "images")
        system_dir = os.path.join(self.images_dir, "system")
        if not os.path.isdir(system_dir):
            self.log(
                f"No prepared system tree in {system_dir}; "
                "run a full build first"
            )
            return None
        self.partition_dirs = {"system": system_dir}

        self.logger.set_progress(90)
        self.logger.set_state("mke2fs")
        try:
            system_size = self._write_image(output_name)
        except Exception:
            traceback.print_exc()
            return None
        if system_size is None:
            return None

        stale_zip = f"{self.output_path}.zip"
        if os.path.exists(stale_zip):
            os.remove(stale_zip)
        self._set_recorded_size(
            os.path.join("out", self.rom_name, "output.txt"), system_size
        )
        self.logger.set_progress(100)
        self.logger.set_state("done")
        return system_size

    @staticmethod
    def _set_recorded_size(path: str, system_size: int) -> None:
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            return
        with open(path, "w") as f:
            f.write(replace_image_size(text, system_size))

    def compress_output(self) -> int:
        destination = f"{self.output_path}.zip"
        try:
            with tempfile.TemporaryDirectory(
                prefix="compress-", dir=os.path.dirname(destination)
            ) as staging:
                archive = os.path.join(staging, "image.zip")

                with zipfile.ZipFile(
                    archive,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                    allowZip64=True,
                ) as package:
                    package.write(
                        f"{self.output_path}.img", arcname="system.img"
                    )

                if (
                    not os.path.isfile(archive)
                    or os.path.getsize(archive) == 0
                ):
                    return -1
                os.replace(archive, destination)
        except OSError as e:
            self.log(f"Compression failed: {e}")
            return -1
        self.log("Compressed successfully!")
        return 0
