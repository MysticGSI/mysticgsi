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

On macOS, install Homebrew and Xcode Command Line Tools first.

### Automatic setup

```sh
git clone https://github.com/MysticGSI/mysticgsi.git && cd mysticgsi
./setup_host.py     # add --dev to install pytest and Ruff
```

On macOS, Debian/Ubuntu and Arch, the script installs system packages and
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
<summary>NixOS</summary>

```sh
nix --extra-experimental-features 'nix-command flakes' develop
python3 cli.py build <name> <firmware> --type <type>
```

</details>

## Usage

```sh
.venv/bin/python cli.py build <name> <firmware|URL> [--type <type>] [--compress]
.venv/bin/python cli.py rebuild <name> [--compress]
.venv/bin/python cli.py list
.venv/bin/python cli.py clean
```

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
--avb-key /path/to/key.pem - Sign with the specified RSA private key
```

By default, an image is signed with AOSP's AVB RSA-2048 test key. You can pass `--avb-key /path/to/key.pem` to sign with your own RSA private key.

Example:

```sh
.venv/bin/python cli.py build raven \
    https://dl.google.com/dl/android/aosp/raven-up1a.231105.003-factory-76a795d5.zip \
    --type pixel --compress
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
```

Patch files of 50 MiB or more are stored xz-compressed (`<name>.xz`) and unpacked during builds. After adding one, run `./tools/assets.py pack` and commit the `.xz` files (or `.xz.000`, `.xz.001`, ... for split archives).  

`./tools/assets.py status` shows what's packed.

## License

Apache License 2.0, see [LICENSE](LICENSE). Bundled avbtool is MIT-licensed;
see [tools/avb/LICENSE](tools/avb/LICENSE). Third-party files under `patches/`
have separate terms; see [NOTICE](NOTICE).
