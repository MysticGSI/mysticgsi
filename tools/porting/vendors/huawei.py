import os

import fsops

from ..properties import SettingsProp


def get_display_name(ctx):
    local_prop = ctx.props.get("h_product")
    if not local_prop:
        return None

    version = local_prop.get_value("ro.comp.hl.product_base_version")
    android_version = ctx.partition_prop("system").get_android_version()
    result = f"{android_version}.0"
    if version is not None:
        parts = version.split(" ")
        if len(parts) == 2:
            result = parts[1]
        else:
            ctx.log(f"Unexpected EMUI version format: {version}")

    names = {"magicos": "MagicOS", "harmonyos": "HarmonyOS"}
    name = names.get(ctx.rom_type, "EMUI")
    return f"{name} [{result}]"


def patch(ctx):
    # The legacy "hos" type only supplied display naming.
    if ctx.rom_type == "hos":
        return
    system_dir = ctx.partition_dirs["system"]
    system = ctx.system_root()
    system_prop = ctx.partition_prop("system")

    preas = ctx.partition_dirs.get("preas")
    if preas:
        for i in ("app", "priv-app"):
            if os.path.exists(os.path.join(preas, i)):
                fsops.move(
                    os.path.join(preas, i, "*"), os.path.join(system, i)
                )

    h_product = ctx.partition_dirs.get("hw_product")  # Huawei
    if not h_product:
        h_product = ctx.partition_dirs.get("product_h")  # Honor

    if not h_product:
        ctx.log(f"No huawei/honor product partition found for {ctx.rom_type}")
        return

    local_prop_path = os.path.join(h_product, "etc", "prop", "local.prop")
    if os.path.exists(local_prop_path):
        local_prop = SettingsProp()
        local_prop.init_from_file(local_prop_path)

        ctx.info.device_manufacturer = local_prop.get_device_manufacturer()
        ctx.info.device_brand = local_prop.get_device_brand()
        ctx.info.device_model = (
            local_prop.get_market_name() or ctx.info.device_model
        )

        fsops.append_file(
            local_prop_path,
            f"{ctx.partition_dirs['system']}/system/build.prop",
        )
        fsops.drop_lines(local_prop_path, "media.settings.xml")
        fsops.drop_lines(
            f"{system_dir}/system/build.prop", "media.settings.xml"
        )

        ctx.props["h_product"] = local_prop

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

                ctx.info.device_model = (
                    region_local_prop.get_market_name()
                    or ctx.info.device_model
                )

                fsops.drop_lines(region_local_prop_path, "media.settings.xml")
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
            for dev in device_oem_dirs:
                device_dir = os.path.join(oem_folder, dev)
                device_prop_path = os.path.join(
                    device_dir, "prop", "local.prop"
                )

                if os.path.exists(device_prop_path):
                    device_prop = SettingsProp()
                    device_prop.init_from_file(device_prop_path)

                    fsops.drop_lines(device_prop_path, "media.settings.xml")

                    if device_prop.get_market_name():
                        ctx.log(f"Using Huawei/Honor device properties: {dev}")

                        ctx.info.device_codename = (
                            device_prop.get_device_name()
                            or ctx.info.device_codename
                        )
                        ctx.info.device_model = device_prop.get_market_name()

                        fsops.append_file(device_prop_path, system_prop.path)
