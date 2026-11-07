# MysticGSI

A tool to build a GSI (Generic System Image) from stock Android firmware.

Supported firmware: 
- full OTA zips (`payload.bin`)  
- fastboot packages  
- `super.img`, sparse images  
- `system.new.dat`  
- Samsung tars  
- Huawei `UPDATE.APP`  
- Unisoc `.pac`  
- LG `.kdz`  
- Oppo `.ozip`  
- QFIL packages  
- Sony `.sin`  
- Pixel factory images  

Partitions can be ext4, EROFS or F2FS.

## Setup

This project requires Python 3.10+.

### Automatic setup

#### macOS / Linux

On macOS, install Homebrew and Xcode Command Line Tools first.

```sh
git clone https://github.com/MysticGSI/mysticgsi.git && cd mysticgsi
./setup_host.py
```

#### Windows

Install Python 3 and Git if you haven't already. Make sure both are added to PATH.
We recommend using WinGet:

```cmd
winget install --id Python.Python.3.14 --exact --source winget
winget install --id Git.Git --exact --source winget
```

```cmd
git clone https://github.com/MysticGSI/mysticgsi.git && cd mysticgsi
python setup_host.py
```

On macOS, Debian/Ubuntu, Arch, Fedora, openSUSE and Windows, the script installs system packages and
creates `.venv`. On NixOS, it checks the development shell. Native image
tools and the Apktool JAR are bundled with the repository.

### Manual setup

Swap `requirements.txt` for `requirements-dev.txt` if you want the dev tools.
The bundled Apktool JAR needs Java; native image tools are under
`tools/bin/<platform>/<architecture>`.

<details>
<summary>macOS</summary>

```sh
xcode-select --install
brew install python@3.13 curl aria2 gpatch openssl@3 openjdk@17
export PATH="$(brew --prefix openjdk@17)/bin:$PATH"
"$(brew --prefix python@3.13)/bin/python3.13" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>Ubuntu / Debian</summary>

```sh
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl aria2 \
    openssl patch ca-certificates libarchive-tools default-jre-headless
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>Arch</summary>

```sh
sudo pacman -Syu --needed python python-pip curl aria2 openssl patch \
    libarchive jre17-openjdk-headless
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>Fedora and others (RHEL, etc)</summary>

```sh
sudo dnf update -y
sudo dnf install -y --skip-unavailable python3 python3-pip curl aria2 openssl patch \
    ca-certificates libarchive java-openjdk-headless
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

</details>

<details>
<summary>openSUSE (Tumbleweed, Leap)</summary>

```sh
sudo zypper refresh
sudo zypper install -y --no-recommends python3 python3-base curl aria2 openssl patch \
    ca-certificates bsdtar 7zip erofs-utils e2fsprogs java-21-openjdk-headless
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Python is versioned on openSUSE (`python313`, `python313-base`); the `python3`
names resolve to whichever version is current. `python3 -m venv` already
bundles pip, so `python3-pip` is not needed. On Leap, use
`java-17-openjdk-headless` if `java-21-openjdk-headless` is unavailable.

</details>

<details>
<summary>NixOS</summary>

```sh
nix --extra-experimental-features 'nix-command flakes' develop
python3 cli.py build <name> <firmware> --type <type>
```

</details>

<details>
<summary>Windows</summary>

```cmd
winget install --id 7zip.7zip --exact --source winget
winget install --id aria2.aria2 --exact --source winget
winget install --id EclipseAdoptium.Temurin.26.JDK --exact --source winget
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

</details>



## Usage

```sh
.venv/bin/python cli.py build <name> <firmware|URL> [--type <type>] [--format <lz4|lz4hc|lzma>] [--compress]
.venv/bin/python cli.py rebuild <name> [--compress]
.venv/bin/python cli.py list
.venv/bin/python cli.py clean
```

On Windows, run from the repository root:

```cmd
.venv\Scripts\python.exe cli.py build test D:\path\to\firmware.tar
```

F2FS extraction on Windows needs a local NTFS working directory that allows
case sensitivity. Use an up-to-date Python for Windows directory permissions.

### Commands

```sh
build - Build a new ROM image
rebuild - Rebuild an existing ROM image
list - List all builds
clean - Clean up all builds
```

### Command-line options

```sh
name - Name of the build
firmware or URL - Path to the firmware or URL to download it from
--type <type> - ROM type; default is auto (use an explicit type if detection fails)
--compress - Compress the output image into a ZIP
--add <tag> - Add a tag to the build name
--no-debloat - Keep the apps the patch set would otherwise remove
--no-merge - Build separate images instead of merging partitions into system
--format <lz4|lz4hc|lzma> - Build EROFS with this compressor instead of ext4
--avb-key /path/to/key.pem - Sign with the specified RSA private key
```

By default, an image is signed with AOSP's AVB RSA-2048 test key. You can pass `--avb-key /path/to/key.pem` to sign with your own RSA private key.

To keep separate system and product partitions:

```sh
.venv/bin/python cli.py build lg firmware.kdz --no-merge --compress
```

This writes `*-system.img`, `*-product.img`, and images for any other partitions
normally merged into system. Partitions already inside system stay there.
The ZIP contains each image as `<partition>.img`; `rebuild` keeps this layout.
These images require a device that mounts the separate partitions.

By default, images are ext4. `--format` builds EROFS instead, typically
30-50% smaller: lz4hc saves less than lzma but reads faster on the device.
The target device's kernel and fstab must support EROFS, and lzma needs
`CONFIG_EROFS_FS_ZIP_LZMA`. `rebuild` keeps the format of the original build.

Example:

```sh
.venv/bin/python cli.py build raven \
    https://dl.google.com/dl/android/aosp/raven-up1a.231105.003-factory-76a795d5.zip \
    --type pixel --compress
```

## Old-device / MediaTek companion projects

This branch documents optional companion projects for downstream Android ROM
work. MysticGSI itself builds images from stock firmware; it does not provide
Android `brunch` targets or automatically include these projects in its output.
Use the tools below only in the appropriate Android build or device workflow,
and review each project's own instructions and licensing before use.

- [MediaTek BPF kernel patcher](https://github.com/R0rt1z2/mtk-bpf-patcher) — a separate tool for patching supported MediaTek kernel images. Its upstream documentation notes testing on Linux 4.14.x and 4.19.x kernels; verify that a kernel is compatible and keep an original backup before attempting a patch.
- [Hardware overlays](https://github.com/Doze-off/vendor_hardware_overlay) — Android product overlay resources intended for integration in a compatible Android ROM build. Follow the repository's build instructions; these overlays are not installed into a MysticGSI image by this project.
- [Treble App](https://github.com/Doze-off/treble_app-doze) — an Android application for Treble ROMs. It must be built and packaged through a compatible Android app/ROM workflow; MysticGSI does not build or bundle it.

These references are informational only. They do not add device-specific MTK support, guarantee that an old device can boot a GSI, or replace device/kernel-specific validation.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

```sh
.venv/bin/ruff check .
```

Patch files of 50 MiB or more are stored xz-compressed (`<name>.xz`) and unpacked during builds. After adding one, run `./tools/assets.py pack` and commit the `.xz` files (or `.xz.000`, `.xz.001`[...]

`./tools/assets.py status` shows what's packed.

## License

Apache License 2.0, see [LICENSE](LICENSE). Bundled avbtool is MIT-licensed;
see [tools/avb/LICENSE](tools/avb/LICENSE). Third-party files under `patches/`
have separate terms; see [NOTICE](NOTICE).
