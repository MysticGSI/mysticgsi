"""AVB signing for raw GSI system images."""

import os
import sys

from Crypto.PublicKey import RSA

import fsops

AVB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "avb")
AVBTOOL = os.path.join(AVB_DIR, "avbtool.py")
TEST_KEY = os.path.join(AVB_DIR, "testkey_rsa2048.pem")


def signing_parameters(key_path=None):
    """Returns the PEM key path and AVB algorithm for its RSA size."""
    path = os.path.abspath(
        os.path.expanduser(TEST_KEY if key_path is None else key_path)
    )
    with open(path, "rb") as stream:
        data = stream.read()
    if not data.startswith(
        (b"-----BEGIN RSA PRIVATE KEY-----", b"-----BEGIN PRIVATE KEY-----")
    ):
        raise ValueError("AVB key must be an unencrypted PEM RSA private key")
    try:
        key = RSA.import_key(data)
    except (ValueError, IndexError, TypeError):
        raise ValueError("Invalid or encrypted AVB RSA private key") from None
    bits = key.size_in_bits()
    if not key.has_private() or bits not in (2048, 4096, 8192):
        raise ValueError(
            "AVB requires a 2048, 4096 or 8192-bit RSA private key"
        )
    return path, f"SHA256_RSA{bits}"


def sign_system_image(
    image, logger=None, key_path=None, partition_name="system"
):
    """Signs and verifies a staged image; returns the avbtool exit code."""
    log = logger or print
    try:
        key, algorithm = signing_parameters(key_path)
    except (OSError, ValueError) as error:
        log(f"Invalid AVB signing key: {error}")
        return 1
    label = "custom AVB key" if key_path else "AOSP AVB test key"
    log(f"Signing {partition_name} image with {label} ({algorithm})")
    command = [sys.executable, AVBTOOL]
    rc = fsops.run(
        command
        + [
            "add_hashtree_footer",
            "--image",
            image,
            "--partition_name",
            partition_name,
            "--partition_size",
            "0",
            "--algorithm",
            algorithm,
            "--key",
            key,
            "--hash_algorithm",
            "sha256",
            "--do_not_generate_fec",
        ]
    )
    if rc == 0:
        rc = fsops.run(
            command
            + [
                "verify_image",
                "--image",
                image,
                "--key",
                key,
            ]
        )
    if rc != 0:
        log(f"AVB signing or verification failed ({rc})")
    return rc
