# MysticGSI

Builds a GSI (Generic System Image) out of stock firmware from almost any
Android phone.

Supported firmware: full OTA zips (`payload.bin`), fastboot packages and
`super.img`, sparse images, `system.new.dat`, Samsung tars, Huawei
`UPDATE.APP`, Unisoc `.pac`, LG `.kdz`, Oppo `.ozip`, QFIL packages, Sony
`.sin`, and Pixel factory images. Partitions can be ext4, EROFS or F2FS
(Linux only).

## Setup

Works on macOS, Ubuntu/Debian, Arch and NixOS.

```sh
git clone https://github.com/MysticGSI/mysticgsi.git && cd mysticgsi
./setup_host.py     # --dev also installs pytest and flake8
```

The script installs the system packages, creates `.venv` and makes sure
`mke2fs.android` and `e2fsdroid` are available (building them if needed).
It needs a `python3` to start from; on a minimal Arch install run
`sudo pacman -S python` first. On NixOS skip it and use `nix develop`.

You need erofs-utils 1.5+ for EROFS firmware (Ubuntu 24.04 or newer) and about
20 GB of free space per build.

### Manual setup

Swap `requirements.txt` for `requirements-dev.txt` if you want the dev tools.

<details>
<summary>macOS</summary>

```sh
xcode-select --install
brew install python@3.13 cmake ninja pkgconf erofs-utils brotli lz4 \
    pcre2 libusb zstd protobuf aria2 apktool gpatch
"$(brew --prefix python@3.13)/bin/python3.13" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/build_android_tools.py
```

</details>

<details>
<summary>Ubuntu / Debian</summary>

```sh
sudo apt-get install python3 python3-venv erofs-utils aria2 patch \
    default-jre-headless curl build-essential cmake ninja-build pkg-config \
    perl golang-go libgtest-dev libusb-1.0-0-dev libpcre2-dev \
    libprotobuf-dev protobuf-compiler libbrotli-dev liblz4-dev libzstd-dev
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/build_android_tools.py
```

Then install [apktool](#apktool-on-linux).

</details>

<details>
<summary>Arch</summary>

```sh
sudo pacman -Syu --needed python erofs-utils aria2 patch \
    jre-openjdk-headless android-tools curl
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then install [apktool](#apktool-on-linux), or `android-apktool-bin` from the AUR
(it needs `jre-openjdk` in place of `jre-openjdk-headless`).

</details>

<details>
<summary>NixOS</summary>

```sh
nix develop
python3 cli.py build <name> <firmware>
```

</details>

#### apktool on Linux

Grab the latest `apktool_<version>.jar` from the
[releases](https://github.com/iBotPeaches/Apktool/releases):

```sh
mkdir -p ~/.local/bin
curl -fL -o ~/.local/bin/apktool.jar \
    https://github.com/iBotPeaches/Apktool/releases/download/v<version>/apktool_<version>.jar
printf '#!/bin/sh\nexec java -jar "$HOME/.local/bin/apktool.jar" "$@"\n' \
    > ~/.local/bin/apktool
chmod +x ~/.local/bin/apktool
```

`~/.local/bin` has to be on your `PATH`.

## Usage

```sh
.venv/bin/python cli.py build <name> <firmware or URL> --type <type> [--compress]
.venv/bin/python cli.py rebuild <name> [--compress]
.venv/bin/python cli.py list
.venv/bin/python cli.py clean
```

The image ends up in `out/<name>/`. `--compress` also zips it, `--add <tag>`
adds a tag to the build name, and `--no-debloat` keeps the apps the ROM's patch
set would otherwise remove.

`--type` picks the patch set for the ROM you're porting (`hyperos`, `coloros`,
`oneui`, `pixel`, ...). See `ls patches/<sdk>` for the list. Without it only
generic patches are applied, unless it's a custom ROM like LineageOS.

Example:

```sh
.venv/bin/python cli.py build raven \
    https://dl.google.com/dl/android/aosp/raven-up1a.231105.003-factory-76a795d5.zip \
    --type pixel --compress
```

The build summary says whether the image is 64-bit only or 32/64-bit. 

To tweak a finished build, edit its system tree in `tmp/<name>/images/system/`
(delete apps, add files) and run `cli.py rebuild <name>`. The image is
rebuilt and resized to fit, without redoing the whole build.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/python -m flake8
```

Patch files over 50 MB are stored xz-compressed (`<name>.xz`) and unpacked
during builds. After adding one, run `./tools/assets.py pack` and commit the
`.xz`. `./tools/assets.py status` shows what's packed.

## License

Apache License 2.0, see [LICENSE](LICENSE). Third-party files under
`patches/` are not covered by it; see [NOTICE](NOTICE).
