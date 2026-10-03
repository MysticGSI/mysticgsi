class SettingsProp:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.path: str = ""

    def init_from_file(self, path: str) -> None:
        self.path = path.replace("//", "/")

        with open(self.path, "r") as f:
            data = f.read()
            for i in data.split("\n"):
                if i.startswith("#") or not i:
                    continue

                key, sep, value = i.partition("=")
                if sep:
                    self.values[key] = value

    def get_value(self, key: str) -> str | None:
        return self.values.get(key)

    def first_of(self, *keys: str) -> str | None:
        """Like `get_value(a) or get_value(b) or ...`."""
        for key in keys:
            value = self.values.get(key)
            if value:
                return value
        return None

    def starts_with(self, key: str) -> dict[str, str]:
        dictionary = {}
        for i in self.values:
            if i.startswith(key):
                dictionary[i] = self.values[i]

        return dictionary

    def exists(self, key: str) -> bool:
        return key in self.values

    def exists_any(self, keys: list[str]) -> list[str]:
        return [k for k in keys if k in self.values]

    def is_true(self, key: str) -> bool:
        return self.values.get(key) in ("1", "true")

    def is_false(self, key: str) -> bool:
        return not self.is_true(key)

    def get_device_brand(self) -> str | None:
        return self.first_of(
            "ro.product.odm.brand",
            "ro.product.brand",
            "ro.product.vendor.brand",
            "ro.product.system.brand",
            "ro.product.product.brand",
        )

    def get_build_id(self) -> str | None:
        return self.first_of(
            "ro.system.build.id", "ro.product.build.id", "ro.build.id"
        )

    def get_display_build_id(self) -> str:
        return str(
            self.first_of(
                "ro.build.display.id", "ro.system.build.id", "ro.build.id"
            )
        )

    def get_sdk_version(self) -> int:
        raw = self.first_of(
            "ro.system.build.version.sdk", "ro.product.build.version.sdk"
        )
        if raw is None:
            raise ValueError(
                "build.prop has no SDK version (ro.system.build.version.sdk"
                " / ro.product.build.version.sdk)"
            )
        return int(raw)

    def get_device_model(self) -> str | None:
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

    def get_device(self) -> str | None:
        return self.first_of(
            "ro.product.odm.device",
            "ro.product.device",
            "ro.product.vendor.device",
            "ro.product.system.device",
            "ro.product.product.device",
        )

    def get_device_name(self) -> str | None:
        return self.first_of(
            "ro.product.odm.name",
            "ro.product.name",
            "ro.product.vendor.name",
            "ro.product.product.name",
            "ro.product.system.name",
        )

    def get_device_manufacturer(self) -> str | None:
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

    def get_market_name(self) -> str | None:
        return self.first_of(
            "ro.config.marketing_name",
            "ro.product.odm.marketname",
            "ro.vendor.oplus.market.name",
        )

    def get_build_fingerprint(self) -> str | None:
        return self.first_of(
            "ro.odm.build.fingerprint",
            "ro.build.fingerprint",
            "ro.product.build.fingerprint",
            "ro.system.build.fingerprint",
        )

    def get_build_flavor(self) -> str | None:
        return self.values.get("ro.build.flavor")

    def get_security_patch(self) -> str | None:
        return self.first_of(
            "ro.huawei.build.version.security_patch",
            "ro.build.version.security_patch",
        )

    def get_build_tags(self) -> str | None:
        return self.first_of("ro.odm.build.tags", "ro.build.tags")

    def get_build_incremental(self) -> str | None:
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
