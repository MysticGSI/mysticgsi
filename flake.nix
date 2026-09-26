{
  description = "MysticGSI -- GSI build pipeline";

  # Resolved through the local flake registry so this reuses the nixpkgs
  # already in the store; flake.lock pins the exact revision afterwards.
  inputs.nixpkgs.url = "flake:nixpkgs";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs:
        let
          # requirements-dev.txt, resolved from nixpkgs instead of pip.
          python = pkgs.python313.withPackages (ps: with ps; [
            requests         # naming downloads from Content-Disposition
            textual
            protobuf         # payload.bin manifests
            zstandard
            lz4
            brotli
            pycryptodome     # OZIP decryption
            py7zr
            pytest
            flake8
            pep8-naming
          ]);

          # External commands the pipeline invokes by name.
          runtimeTools = with pkgs; [
            aria2            # URL builds (cli.py fetch)
            e2fsprogs
            erofs-utils      # fsck.erofs for EROFS partitions
            android-tools    # mke2fs.android, e2fsdroid
            apktool          # framework jar patches
            gnupatch
            libarchive       # bsdtar, for RAR firmware packages
            openssl          # AVB image signing
          ];
        in
        {
          default = pkgs.mkShell {
            name = "mysticgsi";
            packages = [ python ] ++ runtimeTools;
            shellHook = ''
              export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
              export PYTHONDONTWRITEBYTECODE=1

              # tools/host/env.py prefers these over PATH.
              export MKE2FS=${pkgs.android-tools}/bin/mke2fs.android
              export E2FSDROID=${pkgs.android-tools}/bin/e2fsdroid
              echo "mysticgsi dev shell -- $(python3 --version)"
              echo "  run the tests:  python3 -m pytest tests -q"
            '';
          };
        });
    };
}
