import os
import re

import fsops


def get_rom_name(ctx):
    if ctx.rom_type == "alos":
        return "AluminiumOS"
    return None


def patch(ctx):
    product = ctx.partition_dirs["product"]
    system_ext = ctx.partition_dirs.get("system_ext")

    # Pixel's system_ext makes audioserver wait for Tensor's audio
    # parser (IHalAdapterVendorExtension), which can't run on other
    # vendors; audio then never comes up, and init restarts audioserver
    # every time the parser dies.
    audio_extension_prop = "ro.audio.ihaladaptervendorextension_enabled"
    audio_parser = "vendor.google.whitechapel.audio.hal.parserservice"
    system_ext_prop = ctx.partition_prop("system_ext")
    if system_ext_prop and system_ext_prop.is_true(audio_extension_prop):
        fsops.set_props(system_ext_prop.path, audio_extension_prop, "false")
    if system_ext:
        fsops.rmrf(os.path.join(system_ext, "bin/hw", audio_parser))
        fsops.rmrf(os.path.join(system_ext, "etc/init", f"{audio_parser}.rc"))

    dark_bootanimation_path = os.path.join(
        product, "media", "bootanimation-dark.zip"
    )
    if os.path.exists(dark_bootanimation_path):
        bootanimation_path = os.path.join(
            product, "media", "bootanimation.zip"
        )

        fsops.rmrf(bootanimation_path)
        fsops.move(dark_bootanimation_path, bootanimation_path)


def after_rom_patches(ctx):
    if ctx.rom_type != "alos":
        return

    system = ctx.system_root()
    system_ext = ctx.partition_dirs.get("system_ext")
    product_prop = ctx.partition_prop("product")

    matrix = os.path.join(system, "etc/vintf/compatibility_matrix.device.xml")
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
        "android.hardware.security.keymint."
        "IRemotelyProvisionedComponent/strongbox",
        "android.media.audio.IHalAdapterVendorExtension/default",
        "com.google.android.system.desktop.IGscutilExecutor/default",
    ):
        fsops.drop_lines(contexts, r"^" + re.escape(service) + r"\s")

    sepolicy = os.path.join(system_ext, "etc/selinux/system_ext_sepolicy.cil")
    for path in (
        "/mnt/super_partition_utils",
        "/firmware/vpd/ro/attested_device_id",
        "/firmware/vpd/ro/serial_number",
    ):
        fsops.drop_lines(
            sepolicy, r'^\(genfscon [^ ]+ "' + re.escape(path) + r'" '
        )
