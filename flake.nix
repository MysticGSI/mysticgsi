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
            protobuf         # payload.bin manifests
            zstandard
            lz4
            brotli
            pycryptodome     # OZIP decryption
            py7zr
            pytest
            ruff
          ]);

          # External commands the pipeline invokes by name.
          runtimeTools = with pkgs; [
            aria2            # URL builds (cli.py fetch)
            curl
            jdk17_headless   # run the bundled Apktool JAR
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
              echo "mysticgsi dev shell -- $(python3 --version)"
              echo "  run the tests:  python3 -m pytest tests -q"
            '';
          };
        });
    };
}
