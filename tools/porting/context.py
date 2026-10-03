import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import fsops

from .properties import SettingsProp

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


@dataclass
class DeviceInfo:
    device_brand: str | None = "unknown"
    device_manufacturer: str | None = "unknown"
    device_model: str | None = "unknown"
    device_codename: str | None = "unknown"
    android_version: str = ""
    android_sdk: str = ""
    build_fingerprint: str | None = ""
    build_type: str = ""
    build_id: str = ""
    security_patch: str = ""
    build_tags: str = ""
    build_incremental: str = ""


@dataclass
class PatchContext:
    partition_dirs: dict[str, str]
    image_files: dict[str, str]
    log: Callable[[str], None]
    patches_dir: str = "patches"
    rom_type: str = "auto"
    override_rom_type: str = "default"
    debloat: bool = True
    props: dict[str, SettingsProp] = field(default_factory=dict)
    info: DeviceInfo = field(default_factory=DeviceInfo)
    is_64bit_only: bool = False
    programs_32bit_only: list[str] = field(default_factory=list)

    def system_root(self) -> str:
        system = self.partition_dirs["system"]

        if os.path.exists(os.path.join(system, "build.prop")):
            return system

        # system-as-root layout (Android 9+)
        return os.path.join(system, "system")

    def part_prop(self, part_name: str) -> SettingsProp | None:
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
        # Multi-model firmware keeps device overrides beside generic props.
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

    def partition_prop(self, partition: str) -> SettingsProp | None:
        if not self.partition_dirs.get(partition):
            return None

        if partition == "odm":
            return self.part_prop("odm")

        if partition in self.props:
            return self.props[partition]

        path = self.partition_dirs[partition]
        if partition == "system":
            path = self.system_root()

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

    def reload_props(
        self, partition: str, path: str | None = None
    ) -> SettingsProp | None:
        if path is None:
            self.props.pop(partition, None)
            if partition in ("vendor", "odm"):
                return self.part_prop(partition)
            return self.partition_prop(partition)
        reloaded = SettingsProp()
        reloaded.init_from_file(path)
        self.props[partition] = reloaded
        return reloaded

    def resolve_partitions(self) -> None:
        product = self._find_partition_dir("product")
        if not product:
            raise RuntimeError("product not found")
        self.partition_dirs["product"] = product
        # Patch sets append here even when the ROM ships no product props.
        if not self.partition_prop("product"):
            fsops.touch(os.path.join(product, "etc/build.prop"))

        system_ext = self._find_partition_dir("system_ext")
        if system_ext:
            self.partition_dirs["system_ext"] = system_ext
        odm = self._find_odm_dir()
        if odm:
            self.partition_dirs["odm"] = odm

    def capture_info(self) -> None:
        self.part_prop("vendor")
        system_prop = self.partition_prop("system")
        product_prop = self.partition_prop("product")
        odm_prop = self.part_prop("odm")
        self.info = DeviceInfo(
            android_version=str(system_prop.get_android_version()),
            android_sdk=str(system_prop.get_sdk_version()),
            build_fingerprint=self.first_prop(
                (odm_prop, product_prop, system_prop),
                SettingsProp.get_build_fingerprint,
                "",
            ),
            build_type=self.first_prop(
                (product_prop, system_prop), SettingsProp.get_build_flavor, ""
            ),
            build_id=system_prop.get_build_id() or "",
            security_patch=system_prop.get_security_patch() or "",
            build_tags=self.first_prop(
                (odm_prop, product_prop, system_prop),
                SettingsProp.get_build_tags,
                "",
            ),
            device_brand=self.first_prop(
                (
                    odm_prop,
                    self.part_prop("vendor"),
                    product_prop,
                    system_prop,
                ),
                SettingsProp.get_device_brand,
                "unknown",
            ),
            device_model=self._get_device_model(),
            device_manufacturer=self._get_device_manufacturer(),
            device_codename=self._get_device(),
            build_incremental=self._get_build_incremental(),
        )

    def build_summary(self) -> str:
        info = self.info
        architecture = "64-bit only" if self.is_64bit_only else "32/64-bit"
        return (
            f"Device brand: {info.device_brand}\n"
            f"Device manufacturer: {info.device_manufacturer}\n"
            f"Device model: {info.device_model}\n"
            f"Device codename: {info.device_codename}\n"
            f"Device board: {self._get_board()}\n"
            f"Android version: {info.android_version}\n"
            f"Android SDK: {info.android_sdk}\n"
            f"Build fingerprint: {info.build_fingerprint}\n"
            f"Build type: {info.build_type}\n"
            f"Build tags: {info.build_tags}\n"
            f"Build ID: {info.build_id}\n"
            f"Security patch: {info.security_patch}\n"
            f"Architecture: {architecture}\n"
        )

    def is_android_version(self, target: int | str) -> bool:
        ver = self.info.android_version
        target_str = str(target)
        if ver == target_str:
            return True

        if CODENAME_MAP.get(ver) == target_str:
            return True

        sdk = str(self.partition_prop("system").get_sdk_version())
        if target_str.isdigit() and target_str in SDK_MAP:
            next_sdk = SDK_MAP.get(str(int(target_str) + 1))
            if next_sdk:
                return int(SDK_MAP[target_str]) <= int(sdk) < int(next_sdk)
        return SDK_MAP.get(target_str) == sdk

    def is_android_at_least(self, minimal_version: int) -> bool:
        sdk = SDK_MAP.get(str(minimal_version))
        if sdk is None:
            return False
        return self.partition_prop("system").get_sdk_version() >= int(sdk)

    @staticmethod
    def first_prop(
        props: Iterable[SettingsProp | None],
        getter: Callable[[SettingsProp], str | None],
        default: str,
    ) -> str:
        for prop in props:
            if prop:
                val = getter(prop)
                if val:
                    return val
        return default

    def _get_device_model(self) -> str:
        odm_prop = self.part_prop("odm")
        if odm_prop:
            val = odm_prop.get_market_name() or odm_prop.get_device_model()
            if val:
                return val

        return self.first_prop(
            (
                self.part_prop("vendor"),
                self.partition_prop("product"),
                self.partition_prop("system"),
            ),
            SettingsProp.get_device_model,
            "unknown",
        )

    def _get_device_manufacturer(self) -> str:
        return self.first_prop(
            (
                self.part_prop("odm"),
                self.partition_prop("system"),
                self.partition_prop("product"),
                self.part_prop("vendor"),
            ),
            SettingsProp.get_device_manufacturer,
            "unknown",
        )

    def _get_device(self) -> str:
        return self.first_prop(
            (
                self.part_prop("odm"),
                self.part_prop("vendor"),
                self.partition_prop("product"),
                self.partition_prop("system"),
            ),
            SettingsProp.get_device,
            "unknown",
        )

    def _get_build_incremental(self) -> str:
        return self.first_prop(
            (
                self.partition_prop("system"),
                self.partition_prop("product"),
            ),
            SettingsProp.get_build_incremental,
            "",
        )

    def _get_board(self) -> str:
        vendor_prop = self.partition_prop("vendor")
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
                and not fsops.islink(path)
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
