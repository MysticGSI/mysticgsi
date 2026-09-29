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
from assets import ensure_extracted
from tools.config import DEFAULT_PARTITIONS
from tools.isa import find_cpu_features

APKTOOL_JAR = os.path.join(os.path.dirname(__file__), "tools", "apktool", "apktool.jar")


def apktool(*args, cwd=None):
    return fsops.run(["java", "-jar", APKTOOL_JAR, *args], cwd=cwd)


SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_name(value, fallback="unknown"):
    value = UNSAFE_CHARS.sub("_", str(value)).strip("._-")
    return value[:96] or fallback


def safe_name(value, what):
    value = str(value)
    if not SAFE_NAME.match(value) or value in (".", ".."):
        raise ValueError(
            f"invalid {what}: {value!r} -- allowed: letters, digits, dot, "
            f"dash, underscore (max 64 chars)"
        )
    return value


SIZE_UNITS = ["Bytes", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"]


def bytes_to_human(b: int):
    d = ""
    s = 0

    while b > 1024:
        d = f".{b % 1024 * 100 // 1024:02d}"
        b //= 1024
        s += 1

    return f"{b}{d} {SIZE_UNITS[s]}"


# 32-bit runtime pieces that always ship alongside their 64-bit twins.
PAIRED_32BIT_PROGRAMS = ("linker", "linker_asan")


def find_32bit_only_programs(bin_dirs):
    """Programs in bin_dirs that exist only as 32-bit ELF executables."""
    found = set()
    for bin_dir in bin_dirs:
        if not os.path.isdir(bin_dir):
            continue
        for name in os.listdir(bin_dir):
            path = os.path.join(bin_dir, name)
            if (
                name.endswith("32")
                or name in PAIRED_32BIT_PROGRAMS
                or os.path.islink(path)
                or not os.path.isfile(path)
            ):
                continue
            try:
                with open(path, "rb") as f:
                    header = f.read(5)
            except OSError:
                continue
            if header == b"\x7fELF\x01":
                found.add(name)
    return sorted(found)


def replace_image_size(text, system_size):
    """Updates the "Raw Image Size:" line of a build summary."""
    return re.sub(
        r"Raw Image Size: [^\n<]*",
        f"Raw Image Size: {bytes_to_human(system_size)}",
        text,
    )


patches_dir = "patches"
tmp_dir = "tmp"
output_dir = "out"

SDK_MAP = {
    "10": "29",
    "11": "30",
    "12": "31",
    "13": "33",
    "14": "34",
    "15": "35",
    "16": "36",
    "17": "37",
    "18": "38",
}
CODENAME_MAP = {
    "R": "11",
    "S": "12",
    "Sv2": "12",
    "Tiramisu": "13",
    "UpsideDownCake": "14",
    "VanillaIceCream": "15",
    "Baklava": "16",
    "CinnamonBun": "17",
}

AB_FILES = [
    "etc/init/bufferhubd.rc",
    "etc/init/cppreopts.rc",
    "etc/init/otapreopt.rc",
    "etc/init/performanced.rc",
    "etc/init/recovery-persist.rc",
    "etc/init/recovery-refresh.rc",
    "etc/init/update_verifier.rc",
    "etc/init/virtual_touchpad.rc",
    "bin/update_verifier",
]

USB_DEBUGGING_PROPS = [
    ("ro.debuggable=0", "ro.debuggable=1"),
    ("ro.secure=1", "ro.secure=0"),
    ("ro.adb.secure=1", "ro.adb.secure=0"),
]


class SettingsProp:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.path: str = ""

    def init_from_file(self, path):
        self.path = path.replace("//", "/")

        with open(self.path, "r") as f:
            data = f.read()
            for i in data.split("\n"):
                if i.startswith("#") or not i:
                    continue

                key, sep, value = i.partition("=")
                if sep:
                    self.values[key] = value

    def get_value(self, key) -> str | None:
        return self.values.get(key)

    def first_of(self, *keys) -> str | None:
        """Like `get_value(a) or get_value(b) or ...`."""
        for key in keys:
            value = self.values.get(key)
            if value:
                return value
        return None

    def starts_with(self, key):
        dictionary = {}
        for i in self.values:
            if i.startswith(key):
                dictionary[i] = self.values[i]

        return dictionary

    def exists(self, key):
        return key in self.values

    def exists_any(self, keys: list[str]) -> list[str]:
        return [k for k in keys if k in self.values]

    def is_true(self, key: str) -> bool:
        return self.values.get(key) in ("1", "true")

    def is_false(self, key: str) -> bool:
        return not self.is_true(key)

    def get_device_brand(self):
        return self.first_of(
            "ro.product.odm.brand",
            "ro.product.brand",
            "ro.product.vendor.brand",
            "ro.product.system.brand",
            "ro.product.product.brand",
        )

    def get_build_id(self):
        return self.first_of(
            "ro.system.build.id", "ro.product.build.id", "ro.build.id"
        )

    def get_display_build_id(self):
        return str(
            self.first_of(
                "ro.build.display.id", "ro.system.build.id", "ro.build.id"
            )
        )

    def get_sdk_version(self):
        raw = self.first_of(
            "ro.system.build.version.sdk", "ro.product.build.version.sdk"
        )
        if raw is None:
            raise ValueError(
                "build.prop has no SDK version (ro.system.build.version.sdk"
                " / ro.product.build.version.sdk)"
            )
        return int(raw)

    def get_device_model(self):
        return self.first_of(
            "ro.product.product.tran.device.name.default",
            "ro.product.en.display",
            "ro.vendor.product.ztename",
            "ro.product.odm.model",
            "ro.product.model",
            "ro.product.vendor.model",
            "ro.product.system.model",
            "ro.product.product.model",
        )

    def get_device(self):
        return self.first_of(
            "ro.product.odm.device",
            "ro.product.device",
            "ro.product.vendor.device",
            "ro.product.system.device",
            "ro.product.product.device",
        )

    def get_device_name(self):
        return self.first_of(
            "ro.product.odm.name",
            "ro.product.name",
            "ro.product.vendor.name",
            "ro.product.product.name",
            "ro.product.system.name",
        )

    def get_device_manufacturer(self):
        return self.first_of(
            "ro.product.odm.manufacturer",
            "ro.product.manufacturer",
            "ro.product.vendor.manufacturer",
            "ro.product.system.manufacturer",
            "ro.product.product.manufacturer",
        )

    def get_android_version(self) -> str:
        codename = self.values.get("ro.build.version.codename")
        known_codenames = self.values.get("ro.build.version.known_codenames")
        if known_codenames and codename and str(codename) in known_codenames:
            return self.values.get("ro.build.version.codename")

        raw = self.first_of(
            "ro.system.build.version.release", "ro.build.version.release"
        )
        if raw is None:
            raise ValueError(
                "build.prop has no Android version "
                "(ro.system.build.version.release / "
                "ro.build.version.release)"
            )
        return raw

    def get_market_name(self):
        return self.first_of(
            "ro.config.marketing_name",
            "ro.product.odm.marketname",
            "ro.vendor.oplus.market.name",
        )

    def get_build_fingerprint(self):
        return self.first_of(
            "ro.odm.build.fingerprint",
            "ro.build.fingerprint",
            "ro.product.build.fingerprint",
            "ro.system.build.fingerprint",
        )

    def get_build_flavor(self):
        return self.values.get("ro.build.flavor")

    def get_security_patch(self):
        return self.first_of(
            "ro.huawei.build.version.security_patch",
            "ro.build.version.security_patch",
        )

    def get_build_tags(self):
        return self.first_of("ro.odm.build.tags", "ro.build.tags")

    def get_build_incremental(self):
        return self.first_of(
            "ro.build.version.incremental",
            "ro.system.build.version.incremental",
            "ro.product.build.version.incremental",
        )

    def get_hyperos_version(self) -> str:
        if self.exists("ro.mi.os.version.incremental"):
            version = str(self.values.get("ro.mi.os.version.incremental"))
            return version.split("OS")[1]

        return ""


class StubLogger:
    def set_progress(self, progress: int): ...

    def set_state(self, state: str): ...

    def add(self, message):
        print(message)


class RomPorter:
    PARTITION_NAMES: ClassVar[list[str]] = DEFAULT_PARTITIONS

    def __init__(self, rom_name, variant_tag=""):
        self.images_dir = ""
        self.logger = StubLogger()
        self.image_files = {}
        self.partition_dirs: dict[str, str] = {}
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
        self.props: dict[str, SettingsProp] = {}
        self.cpu_warning = ""

    def log(self, message):
        self.logger.add(message)

    def build(self, filename):
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

    def _unpack_image(self, name, path, out):
        fs = tools.detect_filesystem(path)
        self.log(f"Unpacking {name} ({fs})")
        rc = tools.unpack_filesystem(path, out, fs_type=fs, logger=self.log)
        if rc != 0:
            return -1
        labels = tools.read_labels(path, fs, logger=self.log)
        if labels is not None:
            self.stock_labels[name] = labels
        return 0

    def _unpack_partitions(self):
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

    def _extract_firmware(self, archive_path):
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

    def _detect_rom_type(self):
        if self.rom_type not in ("generic", "custom", "auto"):
            return

        if self.override_rom_type != "default":
            self.rom_type = self.override_rom_type
            return

        custom_rom_props = {
            "lineageOS": ["ro.lineage.build.version"],
            "evolutionx": ["org.evolution.build_version"],
            "crDroid": ["ro.crdroid.version"],
            "pixelexperience": ["org.pixelexperience.version"],
            "projectblaze": ["org.blaze.version"],
            "risingos": ["ro.rising.version"],
            "voltageos": ["org.voltage.version"],
        }

        system_prop = self._get_partition_prop("system")
        product_prop = self._get_partition_prop("product")
        props = (system_prop, product_prop)

        for rom, value in custom_rom_props.items():
            for prop in props:
                if not prop:
                    continue
                for key in value:
                    if prop.exists(key):
                        self.rom_type = rom
                        return

        if self.rom_type != "auto":
            return

        vendor_rom_props = {
            "oneui": "ro.build.version.oneui",
            "hyperos": "ro.mi.os.version.incremental",
            "miui": "ro.miui.ui.version.name",
            "nothing": "ro.nothing.version.id",
        }
        for rom, key in vendor_rom_props.items():
            if any(prop and prop.get_value(key) for prop in props):
                self.rom_type = rom
                return

        brands = {
            prop.get_device_brand().casefold()
            for prop in props
            if prop and prop.get_device_brand()
        }
        if any(
            prop and prop.get_value("ro.build.version.oplusrom")
            for prop in props
        ):
            oplus_types = {
                "oppo": "coloros",
                "realme": "realmeui",
                "oneplus": "oxygenos",
            }
            for brand in ("oppo", "realme", "oneplus"):
                if brand in brands:
                    self.rom_type = oplus_types[brand]
                    return

        honor_brand = "honor" in brands
        for partition in ("product_h", "hw_product"):
            part_dir = self.partition_dirs.get(partition)
            if not part_dir:
                continue
            local_prop_path = os.path.join(
                part_dir, "etc", "prop", "local.prop"
            )
            if not os.path.isfile(local_prop_path):
                continue
            local_prop = SettingsProp()
            local_prop.init_from_file(local_prop_path)
            if partition == "product_h":
                honor_brand = honor_brand or (
                    (local_prop.get_device_brand() or "").casefold()
                    == "honor"
                )
            version = (
                local_prop.get_value("ro.comp.hl.product_base_version") or ""
            ).casefold()
            for rom in ("magicos", "harmonyos", "emui"):
                if version.startswith(rom):
                    self.rom_type = rom
                    return

        if self.partition_dirs.get("product_h") and honor_brand:
            self.rom_type = "magicos"

    def _rom_type_name(self):
        if self.rom_type == "alos":
            return "AluminiumOS"
        return self.rom_type.capitalize()

    def get_display_name(self):
        system_prop = self._get_partition_prop("system")
        self.android_version = str(system_prop.get_android_version())
        product_prop = self._get_partition_prop("product")

        try:
            # OneUI
            if self.rom_type == "oneui":
                # e.g. 60101 -> 6.1.1, 60100 -> 6.1
                oneui_version = system_prop.get_value("ro.build.version.oneui")
                if oneui_version and len(oneui_version) == 5:
                    oneui_major = oneui_version[0]
                    oneui_build = oneui_version[2]
                    oneui_minor = oneui_version[4]

                    version = f"{oneui_major}.{oneui_build}"
                    if oneui_minor != "0":
                        version += f".{oneui_minor}"

                    return f"OneUI {version}"
                else:
                    return f"OneUI {self.android_version}"

            # MIUI
            if self.rom_type == "miui":
                return f"MIUI [{system_prop.get_build_incremental()}]"

            # HyperOS
            if self.rom_type == "hyperos":
                return f"HyperOS [{system_prop.get_hyperos_version()}]"

            # EMUI/MagicOS/HarmonyOS
            if self.rom_type in ("emui", "magicos", "harmonyos", "hos"):
                local_prop = self.props.get("h_product")
                if local_prop:
                    emui_version = local_prop.get_value(
                        "ro.comp.hl.product_base_version"
                    )
                    emui_result = f"{self.android_version}.0"
                    if emui_version is not None:
                        emui_version_split = emui_version.split(" ")
                        if len(emui_version_split) == 2:
                            emui_result = emui_version_split[1]
                        else:
                            self.log(
                                f"Unexpected EMUI version format: {emui_version}"
                            )

                    name = "EMUI"
                    if self.rom_type == "magicos":
                        name = "MagicOS"
                    if self.rom_type in "harmonyos":
                        name = "HarmonyOS"

                    return f"{name} [{emui_result}]"

            # NothingOS
            if self.rom_type == "nothing":
                version = system_prop.get_value("ro.nothing.version.id")
                return f"NothingOS [{version}]"

            # ZUI
            if self.rom_type == "zui":
                version = system_prop.get_value("ro.external.version.code")
                return f"ZUI [{version}]"

            # ColorOS/RealmeUI/OxygenOS
            if self.rom_type in ("coloros", "realmeui", "oxygenos"):
                oplusrom_version = system_prop.get_value(
                    "ro.build.version.oplusrom"
                )
                if oplusrom_version:
                    oplusrom_version = oplusrom_version.split("V")[1]

                    oplusrom_type = "ColorOS"
                    if self.rom_type == "realmeui":
                        oplusrom_type = "RealmeUI"
                    elif self.rom_type == "oxygenos":
                        oplusrom_type = "OxygenOS"

                    return f"{oplusrom_type} [{oplusrom_version}]"

            # MyOS/NebulaOS
            if self.rom_type in ("myos", "nebulaos"):
                t = "MyOS"
                if self.rom_type == "nebulaos":
                    t = "NebulaOS"

                display_id = system_prop.get_display_build_id()
                version = display_id.split("_")[0].split(t)[1]

                return f"{t} [{version}]"

            # Evolution X
            if self.product_prop.exists("org.evolution.build_version"):
                version = product_prop.get_value("org.evolution.build_version")
                return f"Evolution X {version}"
            # crDroid
            if self.system_prop.exists("ro.crdroid.version"):
                return f"crDroid {system_prop.get_value('ro.modversion')}"
            # Pixel Experience
            if self.system_prop.exists("org.pixelexperience.version"):
                build_type = system_prop.get_value(
                    "org.pixelexperience.build_type"
                )
                return f"PixelExperience {build_type} {self.android_version}.0"
            # Project Blaze
            if self.system_prop.exists("org.blaze.version"):
                version = system_prop.get_value("org.blaze.version")
                build_type = system_prop.get_value("ro.blaze.buildtype")
                return f"Project Blaze {version} ({build_type})"
            # RisingOS
            if self.product_prop.exists(
                "ro.rising.version"
            ) or self.system_prop.exists("ro.rising.version"):

                def rising(key):
                    return product_prop.get_value(
                        key
                    ) or system_prop.get_value(key)

                return (
                    f"RisingOS [{rising('ro.rising.version')}] "
                    f"({rising('ro.rising.releasetype')}, "
                    f"{rising('ro.rising.packagetype')})"
                )
            # VoltageOS
            if self.system_prop.exists("org.voltage.version"):
                version = system_prop.get_value("org.voltage.version")
                return f"VoltageOS [{version}]"
            # LineageOS
            if self.system_prop.exists("ro.lineage.build.version"):
                version = system_prop.get_value("ro.lineage.build.version")
                return f"LineageOS {version}"

        except Exception as e:
            self.log(f"Failed to determine the ROM display name: {e}")
            traceback.print_exc()

        result = self._rom_type_name()
        if not self.android_version.isdigit():
            result += f" Android {self.android_version}"
        else:
            result += f" {float(self.android_version)}"

        if self.variant_tag:
            result += f" {self.variant_tag.replace('-', ' ')}"

        return result

    def _remove_unneded_files(self):
        system = self._get_system_root()
        system_ext = self.partition_dirs.get("system_ext")

        useless_files = [
            # Hey, it's me, it's Verity!
            # Ask me anything!
            "verity_key",
            "init.recovery*",
            "recovery-from-boot.*",
        ]

        for file in useless_files:
            fsops.rmrf(os.path.join(self.partition_dirs["system"], file))

        # Dolphin can crash surfaceflinger on some ROMs.
        dolphin_lib_path = "lib64/libdolphin.so"

        fsops.rmrf(os.path.join(system, dolphin_lib_path))
        if system_ext:
            fsops.rmrf(os.path.join(system_ext, dolphin_lib_path))

        # Remove Dirac apps.
        dirac_apps = ["priv-app/DiracAudioControlService", "app/DiracManager"]

        for app in dirac_apps:
            fsops.rmrf(os.path.join(system, app))

        # Remove Qualcomm location app.
        qcom_location_app = "priv-app/com.qualcomm.location"

        fsops.rmrf(os.path.join(system, qcom_location_app))
        if system_ext:
            fsops.rmrf(os.path.join(system_ext, qcom_location_app))

    def _patch_ramdisk(self):
        sysdir = self.partition_dirs["system"]

        to_remove = ["persist", "bt_firmware", "firmware", "cache"]
        to_symlink = {
            "/vendor/bt_firmware": "bt_firmware",
            "/vendor/firmware": "firmware",
        }

        for i in to_remove:
            fsops.rmrf(os.path.join(sysdir, i))

        for i, value in to_symlink.items():
            fsops.symlink(i, os.path.join(sysdir, value))

    def _patch_selinux(self):
        clean = [
            "ro.opengles.version",
            "sys.usb.configfs",
            "sys.usb.controller",
            "sys.usb.config",
            "ro.build.fingerprint",
            "software.version",
            "miui.reverse.charge",
            "ro.cust.test",
            "persist.sar.mode",
            "opengles.version",
            "actionable_compatible_property.enabled",
            "vendor.vibrator",
            "vendor.camera",
            "ab_ota_partitions",
            "postinstall.fstab",
            "ro.vendor.trusty.storage.fs_ready",
            "ro.vendor.trusty.storage.fs_ready_rw",
        ]

        system = self.partition_dirs["system"]
        system_ext = self.partition_dirs.get("system_ext")
        product = self.partition_dirs["product"]

        plat_property_contexts = os.path.join(
            system, "etc/selinux/plat_property_contexts"
        )
        plat_file_contexts = os.path.join(
            system, "etc/selinux/plat_file_contexts"
        )

        for i in clean:
            fsops.drop_lines(plat_property_contexts, i)
            fsops.drop_lines(plat_file_contexts, i)

            if system_ext and os.path.exists(
                os.path.join(system_ext, "etc/selinux")
            ):
                fsops.drop_lines(
                    os.path.join(
                        system_ext, "etc/selinux/system_ext_property_contexts"
                    ),
                    i,
                )

            if os.path.exists(os.path.join(product, "etc/selinux")):
                fsops.drop_lines(
                    os.path.join(
                        product, "etc/selinux/product_property_contexts"
                    ),
                    i,
                )

        if system_ext:
            fsops.drop_lines(
                os.path.join(
                    system_ext, "etc/selinux/system_ext_sepolicy.cil"
                ),
                "genfscon",
            )

        # Enables logcat.
        fsops.sub_lines(
            plat_file_contexts,
            r"u:object_r:logcat_exec:s0",
            r"u:object_r:logd_exec:s0",
        )

    def _patch_framework_jars(self):
        if not (
            (
                self.rom_type in ("miui", "hyperos")
                and self._is_android_version(14)
            )
            or (self.rom_type == "nothing" and self._is_android_version(13))
        ):
            return
        with tempfile.TemporaryDirectory(
            prefix="framework-", dir=self.work_dir
        ) as workdir:
            self._patch_frameworks(os.path.abspath(workdir))

    def _patch_xiaomi_frameworks(self, workdir):
        if not self._is_android_version(14):
            return

        # Fix brightness in Xiaomi 14 ports (HyperOS 1.0 only).
        system_ext = self.partition_dirs["system_ext"]
        miui_services_path = os.path.join(
            system_ext, "framework", "miui-services.jar"
        )
        out_dir = os.path.join(workdir, "miui-services.jar.out")

        fsops.copy_file(miui_services_path, workdir)
        if (
            apktool(
                "d",
                os.path.join(workdir, "miui-services.jar"),
                "-f",
                "-o",
                out_dir,
            )
            != 0
        ):
            raise RuntimeError("apktool failed to decode miui-services.jar")

        hysteric_path = f"{out_dir}/smali/com/android/server/display/HysteresisLevelsImpl.smali"
        if not os.path.exists(hysteric_path):
            return

        with open(hysteric_path, "r+") as file:
            content = file.read()
            content = content.replace(
                "iget v1, v1, Lcom/android/server/display/"
                "DisplayDeviceConfig$HighBrightnessModeData;->minimumLux:F",
                "const/high16 v1, 0x3f800000    # 1.0f",
            )

            file.seek(0)
            file.write(content)
            file.truncate()

        if apktool("b", cwd=out_dir) != 0:
            raise RuntimeError("apktool failed to rebuild miui-services.jar")
        built = f"{out_dir}/dist/miui-services.jar"
        if not os.path.exists(built):
            raise RuntimeError("apktool produced no miui-services.jar")
        fsops.copy_file(built, miui_services_path)

    def _patch_nothing_frameworks(self, workdir):
        system = self._get_system_root()
        services_path = os.path.join(system, "framework", "services.jar")

        if not self._is_android_version(13):
            return

        out_dir = os.path.join(workdir, "services.jar.out")
        fsops.copy_file(services_path, workdir)
        if (
            apktool(
                "d",
                os.path.join(workdir, "services.jar"),
                "-f",
                "-o",
                out_dir,
            )
            != 0
        ):
            raise RuntimeError("apktool failed to decode services.jar")

        patched = False

        # ChargeLevelUpdater depends on Nothing's HALs and can bootloop.
        charge_updater = (
            f"{out_dir}/smali_classes2/com/nothing/server/"
            "BatteryChargeManager$ChargeLevelUpdater.smali"
        )
        if os.path.exists(charge_updater):
            try:
                with open(charge_updater, "r") as f:
                    lines = f.read().split("\n")
                method_run_index = -1
                locals_index = -1
                for i, line in enumerate(lines):
                    if line == ".method public run()V":
                        method_run_index = i
                        continue

                    if method_run_index not in (-1, 0):
                        if ".locals" in line:
                            locals_index = i
                            continue
                        if "return-void" in line:
                            break
                        if locals_index != -1:
                            lines[i] = ""

                with open(charge_updater, "w") as f:
                    f.write("\n".join(lines))
                patched = True
            except Exception as e:
                patched = False
                self.log(f"BatteryChargeManager patch failed: {e}")

        # Patching the refresh rate controller to use the system config.
        refresh_parser = (
            f"{out_dir}/smali_classes2/com/android/server/wm/"
            "NtRefreshRateController$NtRefreshRateFileParser"
            ".smali"
        )
        if os.path.exists(refresh_parser):
            try:
                fsops.sub_lines(
                    refresh_parser,
                    "/vendor/etc/display_refresh_rate_config.json",
                    "/system/mystic/display_refresh_rate_config.json",
                )
                patched = True
            except Exception as e:
                self.log(f"NtRefreshRateController patch failed: {e}")

        if not patched:
            self.log("services.jar patching failed; keeping the stock jar")
        elif apktool("b", cwd=out_dir) != 0:
            self.log(
                "apktool failed to rebuild services.jar; keeping the stock jar"
            )
        elif not os.path.exists(os.path.join(out_dir, "dist", "services.jar")):
            self.log("apktool produced no services.jar; keeping the stock jar")
        else:
            fsops.copy_file(
                os.path.join(out_dir, "dist", "services.jar"), services_path
            )

    def _patch_frameworks(self, workdir):
        if self._is_xiaomi_rom():
            self._patch_xiaomi_frameworks(workdir)
        elif self.rom_type == "nothing":
            self._patch_nothing_frameworks(workdir)

    def patch_init(self):
        system = self._get_system_root()

        # security_setenforce(1) -> security_setenforce(0): permissive SELinux
        self.hexpatch(
            os.path.join(system, "bin", "init"),
            "1F0400710001005420008052",
            "1F0400710001005400008052",
        )

    def hexpatch(self, filepath: str, original: str, patched: str):
        if not os.path.exists(filepath):
            self.log(f"hexpatch: {filepath} not found")
            return

        with open(filepath, "rb+") as file:
            data = file.read().hex().upper()
            data = data.replace(original.upper(), patched.upper())

            file.seek(0)
            file.write(bytes.fromhex(data))
            file.truncate()

    def patch_oplus_binder_monitor(self):
        system_ext = self.partition_dirs.get("system_ext")
        if not system_ext:
            self.log("Skipping oplus binder monitor patch: no system_ext")
            return

        path = os.path.join(system_ext, "lib64", "liboplusbindermonitor.so")

        # cbnz x20, #0x18; mov w0, #0x40  ->  mov w0, #0x38
        # (purpose of the patch is no longer known)

        self.hexpatch(path, "D40000B500088052", "D40000B500078052")

    def _patch_oplus(
        self,
        build_fingerprint,
        device_codename,
        device_brand,
        device_manufacturer,
        device_model,
        systemdir,
    ):
        my_product = self.partition_dirs.get("my_product")
        odm = self.partition_dirs.get("odm")
        system = self._get_system_root()

        self.patch_oplus_binder_monitor()

        # Copy audio policy configurations from my_product and odm to system.
        if my_product and odm:
            my_product_audio_config = os.path.join(
                my_product, "etc", "audio_policy_configuration.xml"
            )
            odm_audio_config = os.path.join(
                odm, "etc", "virtual_audio_policy_configuration.xml"
            )
            mystic_path = (
                "/system/mystic/virtual_audio_policy_configuration.xml"
            )

            if os.path.exists(odm_audio_config):
                fsops.copy_file(
                    odm_audio_config, os.path.join(system, "mystic")
                )

                if os.path.exists(my_product_audio_config):
                    with open(my_product_audio_config, "r+") as f:
                        data = f.read()
                        data = data.replace(
                            "/odm/etc/virtual_audio_policy_configuration.xml",
                            mystic_path,
                        )

                        f.seek(0)
                        f.write(data)
                        f.truncate()

        phh_files = [
            "etc/fake_audio_policy_volume.xml",
            "etc/usb_audio_policy_configuration.xml",
        ]

        for i in phh_files:
            fsops.rmrf(os.path.join(system, i))

        # Merge my_* build.props so the build info reflects the device.
        for partition in self.image_files:
            if not partition.startswith("my_"):
                continue

            partition_dir = self.partition_dirs[partition]
            partition_prop_path = f"{partition_dir}/build.prop"
            if not os.path.exists(partition_prop_path):
                partition_prop_path = f"{partition_dir}/etc/build.prop"
                if not os.path.exists(partition_prop_path):
                    continue

            remove_props = [
                "ro.product.odm.model",
                "ro.product.first_api_level",
                "ro.product.model",
                "ro.product.name",
                "ro.product.vendor.name",
                "ro.product.device",
                "ro.product.vendor.model",
                "ro.product.odm.name",
                "ro.product.bootimage.model",
                "ro.product.bootimage.name",
                "ro.zygote",
            ]

            for i in remove_props:
                fsops.drop_lines(partition_prop_path, i)

            fsops.append_file(
                partition_prop_path, f"{systemdir}/system/build.prop"
            )

            system_prop = SettingsProp()
            system_prop.init_from_file(f"{systemdir}/system/build.prop")

            self.props["system"] = system_prop

            device_brand = system_prop.get_device_brand()
            device_manufacturer = system_prop.get_device_manufacturer()
            device_model = (
                system_prop.get_market_name() or system_prop.get_device_model()
            )
            device_codename = system_prop.get_device()
            device_codename = device_codename
            build_fingerprint = system_prop.get_build_fingerprint()

            self.device_model = device_model
            self.device_codename = device_codename

        return (
            build_fingerprint,
            device_codename,
            device_brand,
            device_manufacturer,
            device_model,
        )

    def _patch_huawei(
        self,
        device_brand,
        device_codename,
        device_manufacturer,
        device_model,
        system_dir,
    ):
        system = self._get_system_root()
        system_prop = self._get_partition_prop("system")

        preas = self.partition_dirs.get("preas")
        if preas:
            for i in ("app", "priv-app"):
                if os.path.exists(os.path.join(preas, i)):
                    fsops.move(
                        os.path.join(preas, i, "*"), os.path.join(system, i)
                    )

        h_product = self.partition_dirs.get("hw_product")  # Huawei
        if not h_product:
            h_product = self.partition_dirs.get("product_h")  # Honor

        if not h_product:
            self.log(
                f"No huawei/honor product partition found for {self.rom_type}"
            )
            return (
                device_brand,
                device_codename,
                device_manufacturer,
                device_model,
            )

        local_prop_path = os.path.join(h_product, "etc", "prop", "local.prop")
        if os.path.exists(local_prop_path):
            local_prop = SettingsProp()
            local_prop.init_from_file(local_prop_path)

            device_manufacturer = local_prop.get_device_manufacturer()
            device_brand = local_prop.get_device_brand()
            device_model = local_prop.get_market_name() or device_model
            self.device_model = device_model

            fsops.append_file(
                local_prop_path,
                f"{self.partition_dirs['system']}/system/build.prop",
            )
            fsops.drop_lines(local_prop_path, "media.settings.xml")
            fsops.drop_lines(
                f"{system_dir}/system/build.prop", "media.settings.xml"
            )

            self.props["h_product"] = local_prop

        region_comm = os.path.join(h_product, "region_comm")
        if os.path.exists(region_comm):
            for region in os.listdir(region_comm):
                region_dir = os.path.join(region_comm, region)
                region_local_prop_path = os.path.join(
                    region_dir, "prop", "local.prop"
                )
                if os.path.exists(region_local_prop_path):
                    region_local_prop = SettingsProp()
                    region_local_prop.init_from_file(region_local_prop_path)

                    device_model = (
                        region_local_prop.get_market_name() or device_model
                    )
                    self.device_model = device_model

                    fsops.drop_lines(
                        region_local_prop_path, "media.settings.xml"
                    )
                    fsops.append_file(region_local_prop_path, system_prop.path)

                system_region_folder = None

                for i in ("emui", "magic"):
                    possible_path = os.path.join(system, i, region)
                    if os.path.exists(possible_path):
                        system_region_folder = possible_path
                        break

                if system_region_folder:
                    for i in ("themes", "media", "screenlock"):
                        fsops.move(
                            os.path.join(region_dir, i, "*"),
                            os.path.join(system_region_folder, i),
                        )

        oem_folder = None

        for i in ("hw_oem", "hn_oem"):
            possible_path = os.path.join(h_product, i)
            if os.path.exists(possible_path):
                oem_folder = possible_path
                break

        if oem_folder:
            device_oem_dirs = os.listdir(oem_folder)
            if device_oem_dirs:
                # looking for a device with the marketing name
                for dev in device_oem_dirs:
                    device_dir = os.path.join(oem_folder, dev)
                    device_prop_path = os.path.join(
                        device_dir, "prop", "local.prop"
                    )

                    if os.path.exists(device_prop_path):
                        device_prop = SettingsProp()
                        device_prop.init_from_file(device_prop_path)

                        fsops.drop_lines(
                            device_prop_path, "media.settings.xml"
                        )

                        if device_prop.get_market_name():
                            self.log(f"_patch_huawei: using {dev}")

                            # we found it!
                            device_codename = (
                                device_prop.get_device_name()
                                or device_codename
                            )
                            device_model = device_prop.get_market_name()

                            fsops.append_file(
                                device_prop_path, system_prop.path
                            )

                            self.device_codename = device_codename
                            self.device_model = device_model

        return (
            device_brand,
            device_codename,
            device_manufacturer,
            device_model,
        )

    def _patch_xiaomi(self, system_dir: str):
        mi_ext = self.partition_dirs.get("mi_ext")
        product = self.partition_dirs["product"]
        system = os.path.join(system_dir, "system")

        device_features_path = os.path.join(product, "etc/device_features")
        if self._is_android_version(11):
            device_features_path = (
                f"{self.partition_dirs['system']}/mystic/device_features"
            )
        elif not self._is_android_at_least(10):
            device_features_path = f"{system}/etc/device_features"

        fsops.cp_r(
            "patches/miui_device_features/*",
            device_features_path,
            clobber=False,
        )

        # mi_ext carries the HyperOS logo and version.
        if mi_ext:
            fsops.cp_r(f"{mi_ext}/product/*", product)
            fsops.cp_r(f"{mi_ext}/system/*", system)
            fsops.rmrf(f"{mi_ext}/system/*")
            fsops.rmrf(f"{mi_ext}/product/*")
            fsops.append_file(
                f"{mi_ext}/etc/build.prop", f"{system}/build.prop"
            )

            # Reload so the mi_ext props appended above are visible later.
            system_prop = SettingsProp()
            system_prop.init_from_file(os.path.join(system, "build.prop"))

            self.props["system"] = system_prop

        if product:
            poco_launcher_path = os.path.join(
                product, "priv-app", "MiLauncherGlobal"
            )

            if os.path.exists(poco_launcher_path):
                fsops.append_text(
                    os.path.join(system, "build.prop"),
                    "\nro.miui.product.home=com.mi.android.globallauncher",
                )

    def _patch_zte(self, vendor_prop: SettingsProp):
        system_path = self._get_system_root()

        features_to_skip = [
            "ro.vendor.feature.zte_feature_awinic_vib",
            "ro.vendor.feature.zte_feature_zperf_cube",
            "ro.vendor.feature.zte_feature_cube_thermallevel_control",
        ]

        vendor_features = vendor_prop.starts_with("ro.vendor.feature")
        for key, value in vendor_features.items():
            if key not in features_to_skip:
                fsops.append_text(
                    os.path.join(system_path, "build.prop"), f"\n{key}={value}"
                )

    def _patch_google(self):
        product = self.partition_dirs["product"]
        system_ext = self.partition_dirs.get("system_ext")

        # Pixel's system_ext makes audioserver wait for Tensor's audio
        # parser (IHalAdapterVendorExtension), which can't run on other
        # vendors; audio then never comes up, and init restarts audioserver
        # every time the parser dies.
        audio_extension_prop = "ro.audio.ihaladaptervendorextension_enabled"
        audio_parser = "vendor.google.whitechapel.audio.hal.parserservice"
        system_ext_prop = self._get_partition_prop("system_ext")
        if system_ext_prop and system_ext_prop.is_true(audio_extension_prop):
            fsops.set_props(
                system_ext_prop.path, audio_extension_prop, "false"
            )
        if system_ext:
            fsops.rmrf(os.path.join(system_ext, "bin/hw", audio_parser))
            fsops.rmrf(
                os.path.join(system_ext, "etc/init", f"{audio_parser}.rc")
            )

        # Replacing the boot animation with the dark variant.
        dark_bootanimation_path = os.path.join(
            product, "media", "bootanimation-dark.zip"
        )
        if os.path.exists(dark_bootanimation_path):
            bootanimation_path = os.path.join(
                product, "media", "bootanimation.zip"
            )

            fsops.rmrf(bootanimation_path)
            fsops.move(dark_bootanimation_path, bootanimation_path)

    def _patch_alos(self):
        system = self._get_system_root()
        system_ext = self.partition_dirs.get("system_ext")
        product_prop = self._get_partition_prop("product")

        matrix = os.path.join(
            system, "etc/vintf/compatibility_matrix.device.xml"
        )
        vpd_hal = (
            r'(?m)^    <hal format="aidl">\n'
            r"        <name>vendor\.google\.desktop\.vpd_executor</name>\n"
            r"        <interface>\n"
            r"            <name>IVpdExecutor</name>\n"
            r"            <instance>default</instance>\n"
            r"        </interface>\n"
            r"    </hal>\n"
        )
        if os.path.isfile(matrix):
            fsops.sub_lines(matrix, vpd_hal, "")

        if product_prop:
            if not fsops.set_props(
                product_prop.path, "ro.setupwizard.require_network", "false"
            ):
                fsops.append_text(
                    product_prop.path, "ro.setupwizard.require_network=false"
                )

        for path in (
            "etc/init/gscd.rc",
            "etc/init/gscutil-executor.rc",
            "etc/init/gscutil.rc",
            "etc/init/timberslide.rc",
            "etc/init/udsattestation.rc",
            "etc/vintf/manifest/manifest_gscd.xml",
        ):
            fsops.rmrf(os.path.join(system, path))

        if not system_ext:
            return

        for path in (
            "etc/init/vendor.qti.hardware.qccsyshal@1.2-service.rc",
            "etc/init/vendor.qti.qccsyshal_aidl-service.rc",
            "etc/vintf/manifest/vendor.qti.qccsyshal_aidl-service.xml",
        ):
            fsops.rmrf(os.path.join(system_ext, path))

        contexts = os.path.join(
            system_ext, "etc/selinux/system_ext_service_contexts"
        )
        for service in (
            "android.hardware.security.keymint.IKeyMintDevice/default",
            "android.hardware.gatekeeper.IGatekeeper/default",
            "android.os.IAccessor/ICommService/security_vm_keymint",
            "android.os.IAccessor/IGatekeeper/security_vm_gatekeeper",
            "android.os.IAccessor/sharedsecret/security_vm_shared_secret",
            "android.keymint.trusty.commservice.ICommService/security_vm_keymint",
            "android.os.IAccessor/IKeyMintProvisioningService/security_vm_keymint",
            "android.os.IAccessor/IVmAttestation/default",
            "android.trusty.vm_attestation.IVmAttestation/default",
            "android.hardware.security.keymint.IKeyMintDevice/strongbox",
            "android.hardware.security.sharedsecret.ISharedSecret/strongbox",
            "android.hardware.security.keymint.IRemotelyProvisionedComponent/strongbox",
            "android.media.audio.IHalAdapterVendorExtension/default",
            "com.google.android.system.desktop.IGscutilExecutor/default",
        ):
            fsops.drop_lines(contexts, r"^" + re.escape(service) + r"\s")

        sepolicy = os.path.join(
            system_ext, "etc/selinux/system_ext_sepolicy.cil"
        )
        for path in (
            "/mnt/super_partition_utils",
            "/firmware/vpd/ro/attested_device_id",
            "/firmware/vpd/ro/serial_number",
        ):
            fsops.drop_lines(
                sepolicy, r'^\(genfscon [^ ]+ "' + re.escape(path) + r'" '
            )

    def _get_system_root(self) -> str:
        system = self.partition_dirs["system"]

        if os.path.exists(os.path.join(system, "build.prop")):
            return system

        # system-as-root layout (Android 9+)
        return os.path.join(system, "system")

    # Multi-model firmware ships per-model props next to the generic one;
    # prefer the one matching the model the generic prop names.
    def _get_part_prop(self, part_name: str) -> SettingsProp | None:
        if part_name in self.props:
            return self.props[part_name]
        part = self.partition_dirs.get(part_name)
        if not part:
            return None
        prop_path = os.path.join(
            part, "etc/build.prop" if part_name == "odm" else "build.prop"
        )
        if not os.path.exists(prop_path):
            return None
        prop = SettingsProp()
        prop.init_from_file(prop_path)
        model = prop.get_device_model()
        if part_name == "odm":
            for name in (f"etc/{model}_build.prop", f"etc/{model}.build.prop"):
                if os.path.exists(os.path.join(part, name)):
                    prop.init_from_file(os.path.join(part, name))
        else:
            model_prop = os.path.join(part, f"build_{model}.prop")
            if os.path.exists(model_prop):
                prop.init_from_file(model_prop)
        self.props[part_name] = prop
        return prop

    def _get_partition_prop(self, partition: str) -> SettingsProp | None:
        if not self.partition_dirs.get(partition):
            return None

        if partition == "odm":
            return self._get_part_prop("odm")

        if partition in self.props:
            return self.props[partition]

        path = self.partition_dirs[partition]
        if partition == "system":
            path = self._get_system_root()

        build_prop_path = None
        if os.path.exists(os.path.join(path, "etc/build.prop")):
            build_prop_path = os.path.join(path, "etc/build.prop")
        elif os.path.exists(os.path.join(path, "build.prop")):
            build_prop_path = os.path.join(path, "build.prop")

        if build_prop_path:
            prop = SettingsProp()
            prop.init_from_file(build_prop_path)

            self.props[partition] = prop

            return prop

        return None

    # This may help 32-64 bit devices to boot the 64-bit only systems.
    def _add_64bit_props(self):
        system = self._get_system_root()
        build_prop_path = os.path.join(system, "build.prop")

        fsops.append_text(build_prop_path, "\n# 64-bit only workaround")
        fsops.append_text(build_prop_path, "dalvik.vm.dex2oat64.enabled=true")
        fsops.append_text(build_prop_path, "ro.zygote=zygote64")
        fsops.append_text(build_prop_path, "ro.product.cpu.abilist=arm64-v8a")
        fsops.append_text(build_prop_path, "ro.product.cpu.abilist32=")
        fsops.append_text(
            build_prop_path, "ro.product.cpu.abilist64=arm64-v8a"
        )

    def _clean_build_props(self):
        system_prop_path = self._get_partition_prop("system").path
        product_prop_path = self._get_partition_prop("product").path
        system_ext_prop = self._get_partition_prop("system_ext")

        for prop in (
            "ro.build.system_root_image",
            "ro.build.ab_update",
            "media.settings.xml",
            "ro.actionable_compatible_property.enabled",
            "ro.frp.pst",
        ):
            fsops.drop_lines(system_prop_path, prop)

        for prop in (
            "media.settings.xml",
            "ro.product.ab_ota_partitions",
            "ro.sys.sdcardfs",
            "persist.vendor.debug.sensors.accel_cal",
            "persist.vendor.testing_battery_profile",
            "masterclear.allow_retain_esim_profiles_after_fdr",
            "ro.postinstall.fstab.prefix",
            "ro.gfx.driver.1",
            "graphics.gpu.profiler.support",
            "graphics.gpu.profiler.vulkan_layer_apk",
            "ro.vendor.camera.extensions.package",
            "ro.vendor.camera.extensions.service",
            "ro.frp.pst",
        ):
            fsops.drop_lines(product_prop_path, prop)

        if system_ext_prop:
            fsops.drop_lines(system_ext_prop.path, "media.settings.xml")

    def _detect_64bit_only(self):
        system = self._get_system_root()

        if not os.path.exists(os.path.join(system, "lib", "libandroid.so")):
            self.log("This ROM is 64-bit only")
            self.is_64bit_only = True
            return

        bin_dirs = [os.path.join(system, "bin")] + [
            os.path.join(self.partition_dirs[p], "bin")
            for p in ("system_ext", "product")
            if p in self.partition_dirs
        ]
        self.programs_32bit_only = find_32bit_only_programs(bin_dirs)
        if self.programs_32bit_only:
            self.log(
                "Warning: this image needs a device with 32-bit "
                "support; these programs are 32-bit only: "
                f"{', '.join(self.programs_32bit_only)}"
            )

    def _architecture(self):
        if self.is_64bit_only:
            return "64-bit only"
        return "32/64-bit"

    def _is_android_version(self, target: int | str) -> bool:
        ver = self.android_version
        target_str = str(target)
        if ver == target_str:
            return True

        if CODENAME_MAP.get(ver) == target_str:
            return True

        sdk = str(self._get_partition_prop("system").get_sdk_version())
        if target_str.isdigit() and target_str in SDK_MAP:
            next_sdk = SDK_MAP.get(str(int(target_str) + 1))
            if next_sdk:
                return int(SDK_MAP[target_str]) <= int(sdk) < int(next_sdk)
        return SDK_MAP.get(target_str) == sdk

    def _is_android_at_least(self, minimal_version: int) -> bool:
        sdk = SDK_MAP.get(str(minimal_version))
        if sdk is None:
            return False
        return self._get_partition_prop("system").get_sdk_version() >= int(sdk)

    # _is_android_at_least_11 == self._is_android_at_least(11)
    # _is_android_11 == _is_android_version(11)

    def _first_prop(self, props, getter, default):
        for prop in props:
            if prop:
                val = getter(prop)
                if val:
                    return val
        return default

    def _get_device_model(self) -> str:
        odm_prop = self._get_part_prop("odm")
        if odm_prop:
            val = odm_prop.get_market_name() or odm_prop.get_device_model()
            if val:
                return val

        return self._first_prop(
            (
                self._get_part_prop("vendor"),
                self._get_partition_prop("product"),
                self._get_partition_prop("system"),
            ),
            SettingsProp.get_device_model,
            "unknown",
        )

    def _get_device_codename(self) -> str:
        return self._first_prop(
            (
                self._get_part_prop("odm"),
                self._get_partition_prop("product"),
                self._get_part_prop("vendor"),
                self._get_partition_prop("system"),
            ),
            SettingsProp.get_device_name,
            "unknown",
        )

    def _get_device_manufacturer(self) -> str:
        return self._first_prop(
            (
                self._get_part_prop("odm"),
                self._get_partition_prop("system"),
                self._get_partition_prop("product"),
                self._get_part_prop("vendor"),
            ),
            SettingsProp.get_device_manufacturer,
            "unknown",
        )

    def _get_device(self) -> str:
        return self._first_prop(
            (
                self._get_part_prop("odm"),
                self._get_part_prop("vendor"),
                self._get_partition_prop("product"),
                self._get_partition_prop("system"),
            ),
            SettingsProp.get_device,
            "unknown",
        )

    def _get_build_incremental(self) -> str:
        return self._first_prop(
            (
                self._get_partition_prop("system"),
                self._get_partition_prop("product"),
            ),
            SettingsProp.get_build_incremental,
            "",
        )

    def _apply_generic_patches(self):
        system_prop = self._get_partition_prop("system")
        android_version = str(system_prop.get_android_version())
        patch_version = android_version.split(".", 1)[0]
        patch_path = os.path.join(patches_dir, "all", patch_version)

        if not os.path.exists(patch_path):
            return

        ensure_extracted(patch_path, self.log)

        system = self._get_system_root()
        system_ext = self.partition_dirs.get("system_ext")
        system_prop_path = (self._get_partition_prop("system")).path
        product_prop_path = (self._get_partition_prop("product")).path

        if os.path.exists(os.path.join(patch_path, "system.prop")):
            fsops.append_file(
                os.path.join(patch_path, "system.prop"), system_prop_path
            )

        if os.path.exists(os.path.join(patch_path, "product.prop")):
            fsops.append_file(
                os.path.join(patch_path, "product.prop"), product_prop_path
            )

        fsops.cp_r(f"{patch_path}/system", f"{system}/")
        if system_ext and os.path.exists(
            os.path.join(patch_path, "system_ext")
        ):
            fsops.cp_r(f"{patch_path}/system_ext", f"{system_ext}/../")

        if os.path.exists(os.path.join(patch_path, "file_contexts")):
            fsops.append_file(
                os.path.join(patch_path, "file_contexts"),
                os.path.join(system, "etc", "selinux", "plat_file_contexts"),
            )

    def _copy_missing_vndks(self):
        system_ext = self.partition_dirs.get("system_ext")
        system = self._get_system_root()
        android_sdk = str(self._get_partition_prop("system").get_sdk_version())
        system_prop = self._get_partition_prop("system")
        android_version = str(system_prop.get_android_version())
        if system_ext and os.path.exists(os.path.join(system_ext, "apex")):
            fsops.cp_r(os.path.join(system_ext, "apex"), system)
            fsops.rmrf(os.path.join(system_ext, "apex"))

        # Preview builds report a codename (e.g. VanillaIceCream) instead of
        # a version number, so try that before the SDK level.
        vndk_path = os.path.join(patches_dir, "vndk", android_version)
        if not os.path.exists(vndk_path):
            vndk_path = os.path.join(patches_dir, "vndk", android_sdk)

        if os.path.exists(vndk_path):
            ensure_extracted(vndk_path, self.log)
            fsops.cp_r(f"{vndk_path}/*", system, clobber=False)

    def _put_mystic_build_display_id(self):
        system_prop = self._get_partition_prop("system")
        product_prop = self._get_partition_prop("product")

        props = [
            "ro.build.display.id",
            "ro.product.build.id",
            "ro.system.build.id",
            "ro.build.id",
        ]

        for prop in (system_prop, product_prop):
            # A product build.prop may be empty (see patch()).
            keys = prop.exists_any(props)
            if keys:
                fsops.set_props(
                    prop.path, keys[0], "Ported.Using.MysticGSI.Tool"
                )

    def _nuke_ab_files(self):
        system = self._get_system_root()

        ab_files = list(AB_FILES)

        # NothingOS's StorageManagerService depends on update_engine.
        if self.rom_type != "nothing":
            ab_files.append("etc/init/update_engine.rc")
            ab_files.append("bin/update_engine")

        for i in ab_files:
            fsops.rmrf(os.path.join(system, i))

    def _configure_updatable_apexes(self):
        system = self._get_system_root()
        system_prop_path = self._get_partition_prop("system").path

        apex_updatable = any(
            file.endswith(".apex")
            for _, _, files in os.walk(os.path.join(system, "apex"))
            for file in files
        )

        if apex_updatable:
            fsops.append_text(system_prop_path, "\nro.apex.updatable=true")

    def _strip_reboot_on_failure(self):
        system = self._get_system_root()

        paths = [
            os.path.join(system, "etc/init/hw/init.rc"),
            os.path.join(system, "etc/init/apexd.rc"),
        ]

        for path in paths:
            fsops.drop_lines(path, "reboot_on_failure")

    def _drop_selinux_mappings(self):
        product = self.partition_dirs["product"]
        system_ext = self.partition_dirs.get("system_ext")
        selinux_mapping_path = "etc/selinux/mapping/*"

        fsops.rmrf(os.path.join(product, selinux_mapping_path))
        if system_ext:
            fsops.rmrf(os.path.join(system_ext, selinux_mapping_path))

    def _force_enable_usb_debugging(self):
        system = self._get_system_root()
        prop_files = [self._get_partition_prop("system").path]
        prop_default = os.path.join(system, "etc/prop.default")
        if os.path.exists(prop_default):
            prop_files.append(prop_default)

        for path in prop_files:
            for pattern, repl in USB_DEBUGGING_PROPS:
                fsops.sub_lines(path, pattern, repl)

    def _determine_treble_compatibility(self):
        system = self._get_system_root()
        system_prop = self._get_partition_prop("system")
        if not system_prop.is_true("ro.treble.enabled"):
            self.log("This firmware isn't Treble supported but fine.")
            if os.path.exists(os.path.join(system, "vendor")):
                fsops.rmrf(os.path.join(system, "vendor", "*"))

    def _apply_rom_patches(self):
        self._detect_rom_type()

        system_prop = self._get_partition_prop("system")
        android_version = str(system_prop.get_android_version())
        android_sdk = str(system_prop.get_sdk_version())
        system = self._get_system_root()
        product_prop = self._get_partition_prop("product")
        system_ext_prop = self._get_partition_prop("system_ext")
        system_ext = self.partition_dirs.get("system_ext")
        product = self.partition_dirs["product"]
        patch_path = os.path.join(patches_dir, android_sdk)

        rom_patches_dir = os.path.join(patches_dir, android_sdk, self.rom_type)
        if not re.fullmatch(r"\d+(?:\.\d+)*", android_version):
            rom_patches_dir = os.path.join(
                patches_dir, android_version, self.rom_type
            )

        config = {}

        if not os.path.exists(rom_patches_dir):
            return

        config_path = os.path.join(rom_patches_dir, "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
            except Exception as e:
                self.log(f"Error reading {config_path}: {e}")

        if not config.get("no_device_overlays", False):
            overlay_dir = os.path.join(
                patches_dir,
                "all",
                android_version.split(".", 1)[0],
                "device_overlay",
            )
            overlay_dst = os.path.join(product, "overlay")
            fsops.mkdirp(overlay_dst)
            ensure_extracted(overlay_dir, self.log)
            fsops.cp_r(f"{overlay_dir}/*", overlay_dst)

        if not config.get("use_stock_init", False):
            init_dir = os.path.join(patch_path, "init")
            if os.path.exists(init_dir):
                fsops.cp_r(f"{init_dir}/*", f"{system}/")
            else:
                self.log(f"No init for {android_version}; patching stock init")
                self.patch_init()
        elif self.rom_type in ("magicos", "emui", "harmonyos", "pixel"):
            self.patch_init()

        system_prop_file = os.path.join(rom_patches_dir, "system.prop")
        if os.path.exists(system_prop_file):
            fsops.append_file(system_prop_file, system_prop.path)

        product_prop_file = os.path.join(rom_patches_dir, "product.prop")
        if os.path.exists(product_prop_file) and product_prop:
            fsops.append_file(product_prop_file, product_prop.path)

        system_ext_prop_file = os.path.join(rom_patches_dir, "system_ext.prop")
        if (
            system_ext
            and system_ext_prop
            and os.path.exists(system_ext_prop_file)
        ):
            fsops.append_file(system_ext_prop_file, system_ext_prop.path)

        debloatware = (config.get("debloat") or {}) if self.debloat else {}
        for partition, folders in debloatware.items():
            if partition not in self.partition_dirs:
                continue
            if partition == "system":
                partition_path = system
            else:
                partition_path = self.partition_dirs[partition]
            for folder, apps in folders.items():
                for app in apps:
                    fsops.rmrf(os.path.join(partition_path, folder, app))

        for partition in self.partition_dirs:
            src = os.path.join(rom_patches_dir, partition)
            if os.path.exists(src):
                if partition == "system":
                    dst = system
                else:
                    dst = self.partition_dirs[partition]

                fsops.cp_r(f"{src}/*", f"{dst}/")

        rw_system_add = os.path.join(rom_patches_dir, "rw-system-add.sh")
        if os.path.exists(rw_system_add):
            fsops.append_file(
                rw_system_add, os.path.join(system, "bin/rw-system.sh")
            )

        file_contexts = os.path.join(rom_patches_dir, "file_contexts")
        if os.path.exists(file_contexts):
            fsops.append_file(
                file_contexts,
                os.path.join(system, "etc/selinux/plat_file_contexts"),
            )

        vendor = self.partition_dirs.get("vendor")
        if config.get("add_vendor_stuff", False) and vendor:
            mystic_dir = os.path.join(system, "mystic")
            fsops.mkdirp(mystic_dir)
            fsops.cp_r(os.path.join(vendor, "etc/group"), mystic_dir)
            fsops.cp_r(os.path.join(vendor, "etc/passwd"), mystic_dir)
            if self.rom_type == "nothing":
                fsops.cp_r(
                    os.path.join(
                        vendor, "etc/display_refresh_rate_config.json"
                    ),
                    mystic_dir,
                )
        if system_ext:
            fsops.drop_lines(
                os.path.join(
                    system_ext, "etc/selinux/system_ext_property_contexts"
                ),
                "vendor.camera",
            )

        patches_json = os.path.join(rom_patches_dir, "patches.json")
        if os.path.exists(patches_json):
            self.log("Applying framework patches")
            self._apply_framework_patches(patches_json, rom_patches_dir)
        if self.rom_type == "alos":
            self._patch_alos()

    def _apply_framework_patches(self, patches_json, rom_patches_dir):
        with open(patches_json, "r") as f:
            patches_data = json.load(f)
        for partition in patches_data:
            for framework in patches_data[partition]:
                path = os.path.join(self.partition_dirs[partition], framework)
                if not os.path.exists(path):
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    self._patch_framework(
                        path,
                        framework,
                        patches_data[partition][framework],
                        rom_patches_dir,
                        os.path.join(tmp, "out"),
                    )

    def _patch_framework(
        self, path, framework, patch_names, rom_patches_dir, out_dir
    ):
        # -o because apktool 2 and 3 put the default output in different
        # places; --no-debug-info because apktool 3 dropped its -b form.
        if (
            apktool("d", "-f", "--no-debug-info", "-o", out_dir, path)
            != 0
        ):
            raise RuntimeError(f"apktool failed to decode {framework}")
        for patch_file in patch_names:
            patch_file_path = os.path.join(
                rom_patches_dir, "framework-patches", f"{patch_file}.patch"
            )
            rc = fsops.run(
                ["patch", "-p0", "-s", "-t", "-N", "--no-backup-if-mismatch"],
                cwd=out_dir,
                stdin=patch_file_path,
            )
            if rc != 0:
                raise RuntimeError(
                    f"Failed to apply {patch_file} to {framework} ({rc})"
                )

        with tempfile.TemporaryDirectory(
            prefix="framework-", dir=os.path.dirname(path)
        ) as staging:
            rebuilt = os.path.join(staging, os.path.basename(path))
            if (
                apktool("b", "-o", os.path.abspath(rebuilt), cwd=out_dir)
                != 0
            ):
                raise RuntimeError(f"apktool failed to rebuild {framework}")
            if not os.path.isfile(rebuilt) or os.path.getsize(rebuilt) == 0:
                raise RuntimeError(f"apktool produced no {framework}")
            os.chmod(rebuilt, os.stat(path).st_mode & 0o7777)
            os.replace(rebuilt, path)

    def _find_partition_dir(self, partition: str) -> str | None:
        if partition in self.partition_dirs:
            return self.partition_dirs[partition]

        system = self.partition_dirs["system"]
        candidates = (
            os.path.join(system, partition),
            os.path.join(system, "system", partition),
        )
        for path in candidates:
            if os.path.exists(os.path.join(path, "etc/build.prop")):
                return path
        # Some ROMs (e.g. HarmonyOS) ship product without a build.prop. The
        # root entry is then usually an empty mountpoint or a symlink.
        for path in candidates:
            if (
                os.path.isdir(path)
                and not os.path.islink(path)
                and os.listdir(path)
            ):
                return path

        return None

    def _find_odm_dir(self) -> str | None:
        if "odm" in self.partition_dirs:
            return self.partition_dirs["odm"]

        vendor = self.partition_dirs.get("vendor")
        if not vendor:
            return None

        if os.path.exists(os.path.join(vendor, "odm", "etc/build.prop")):
            return os.path.join(vendor, "odm")

        return None

    def patch(self):
        try:
            product = self._find_partition_dir("product")
            if not product:
                raise RuntimeError("product not found")
            self.partition_dirs["product"] = product
            # Patch sets append product props here; Android loads it if
            # present, so an empty one is fine where the ROM has none.
            if not self._get_partition_prop("product"):
                fsops.touch(os.path.join(product, "etc/build.prop"))

            system_ext = self._find_partition_dir("system_ext")
            if system_ext:
                self.partition_dirs["system_ext"] = system_ext

            odm = self._find_odm_dir()
            if odm:
                self.partition_dirs["odm"] = odm

            system = self._get_system_root()
            system_dir = self.partition_dirs["system"]
            vendor = self.partition_dirs.get("vendor")
            vendor_prop = self._get_part_prop("vendor")

            system_prop = self._get_partition_prop("system")
            product_prop = self._get_partition_prop("product")
            odm_prop = self._get_part_prop("odm")
            android_version = str(system_prop.get_android_version())
            android_sdk = str(system_prop.get_sdk_version())
            build_fingerprint = self._first_prop(
                (odm_prop, product_prop, system_prop),
                SettingsProp.get_build_fingerprint,
                "",
            )
            build_type = self._first_prop(
                (product_prop, system_prop), SettingsProp.get_build_flavor, ""
            )
            build_id = system_prop.get_build_id() or ""
            security_patch = system_prop.get_security_patch() or ""
            build_tags = self._first_prop(
                (odm_prop, product_prop, system_prop),
                SettingsProp.get_build_tags,
                "",
            )
            device_brand = self._first_prop(
                (
                    odm_prop,
                    self._get_part_prop("vendor"),
                    product_prop,
                    system_prop,
                ),
                SettingsProp.get_device_brand,
                "unknown",
            )
            device_model = self._get_device_model()
            device_manufacturer = self._get_device_manufacturer()
            device_codename = self._get_device()

            self.device_model = device_model
            self.device_codename = device_codename
            self.android_version = android_version
            self.build_incremental = self._get_build_incremental()
            self.system_prop = self._get_partition_prop("system")
            self.product_prop = self._get_partition_prop("product")

            self.log(f"Patching Android {self.android_version} firmware")

            self._determine_treble_compatibility()
            self._detect_64bit_only()
            self._clean_build_props()
            self._apply_generic_patches()
            self._copy_missing_vndks()
            self._apply_rom_patches()
            self._put_mystic_build_display_id()

            self._strip_reboot_on_failure()
            self._configure_updatable_apexes()

            if self.is_64bit_only:
                self._add_64bit_props()

            self._nuke_ab_files()
            self._remove_unneded_files()
            self._patch_ramdisk()
            self._patch_selinux()
            self._patch_framework_jars()

            self._force_enable_usb_debugging()

            if vendor:
                mystic_dir = os.path.join(system, "mystic")
                fsops.mkdirp(mystic_dir)
                if self.rom_type == "oneui":
                    fsops.touch(os.path.join(mystic_dir, "nothing"))

                if os.path.exists(os.path.join(vendor, "overlay")):
                    fsops.mkdirp(os.path.join(mystic_dir, "vo"))
                    fsops.cp_r(
                        os.path.join(vendor, "overlay/*"),
                        os.path.join(mystic_dir, "vo"),
                    )

            self._drop_selinux_mappings()

            if self._is_zte_rom() and vendor_prop:
                self._patch_zte(vendor_prop)

            if self.rom_type in ("pixel", "alos"):
                self._patch_google()

            if self._is_xiaomi_rom():
                self._patch_xiaomi(system_dir)

            if self._is_huawei_rom():
                (
                    device_brand,
                    device_codename,
                    device_manufacturer,
                    device_model,
                ) = self._patch_huawei(
                    device_brand,
                    device_codename,
                    device_manufacturer,
                    device_model,
                    system_dir,
                )

            if self._is_oplus_rom():
                (
                    build_fingerprint,
                    device_codename,
                    device_brand,
                    device_manufacturer,
                    device_model,
                ) = self._patch_oplus(
                    build_fingerprint,
                    device_codename,
                    device_brand,
                    device_manufacturer,
                    device_model,
                    system_dir,
                )

            board_platform = self._get_board()

            self.build_info_text = f"""Device brand: {device_brand}
Device manufacturer: {device_manufacturer}
Device model: {device_model}
Device codename: {device_codename}
Device board: {board_platform}
Android version: {android_version}
Android SDK: {android_sdk}
Build fingerprint: {build_fingerprint}
Build type: {build_type}
Build tags: {build_tags}
Build ID: {build_id}
Security patch: {security_patch}
Architecture: {self._architecture()}
"""

        except Exception as e:
            traceback.print_exc()
            self.log(f"Patching failed: {type(e).__name__}: {e}")
            return -1

        return 0

    def _get_board(self) -> str:
        vendor_prop = self._get_partition_prop("vendor")
        if vendor_prop:
            board = vendor_prop.first_of(
                "ro.board.platform", "ro.product.board"
            )
            if board:
                return board

        h_product_prop = self.props.get("h_product")
        if h_product_prop:
            board = h_product_prop.first_of(
                "ro.board.platform", "ro.product.board"
            )
            if board:
                return board

        return "unknown"

    def _is_xiaomi_rom(self):
        return self.rom_type in ("miui", "hyperos", "joyui")

    def _is_zte_rom(self):
        # NebulaOS isn't ZTE but takes the same patches.
        return self.rom_type in ("myos", "redmagic", "nebulaos")

    def _is_huawei_rom(self):
        return self.rom_type in ("harmonyos", "emui", "magicos")

    def _is_oplus_rom(self):
        return self.rom_type in ("realmeui", "oxygenos", "coloros")

    def prepare(self):
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

    def _save_stock_labels(self, placements):
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

    def _warn_cpu_features(self):
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

    def _write_image(self, output_name):
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

    def _create_system_image(self):
        date = datetime.datetime.now().strftime("%Y%m%d")
        try:
            self.rom_type.capitalize()
        except Exception:
            self.rom_type = "generic"
        output_name = sanitize_name(
            f"{self._rom_type_name()}-{self.device_codename}"
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

    def rebuild(self, output_name):
        """
        Rebuilds the image from the system tree a previous build left in
        tmp/<rom_name>/images/system, e.g. after debloating it by hand.
        Returns the new raw image size in bytes, or None.
        """
        self.images_dir = os.path.join(self.work_dir, "images")
        system_dir = os.path.join(self.images_dir, "system")
        if not os.path.isdir(system_dir):
            self.log(
                f"No prepared system tree in {system_dir}; run a full build first"
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
    def _set_recorded_size(path, system_size):
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            return
        with open(path, "w") as f:
            f.write(replace_image_size(text, system_size))

    def compress_output(self):
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
