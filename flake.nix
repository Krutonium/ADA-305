{
  description = "Safe Linux control for Altec Lansing ADA305 USB speakers";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      eachSystem = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = eachSystem (
        pkgs:
        let
          ada305 = pkgs.python3Packages.buildPythonPackage {
            pname = "ada305";
            version = "0.1.0";
            pyproject = true;
            src = ./.;

            build-system = [ pkgs.python3Packages.setuptools ];
            propagatedBuildInputs = [
              pkgs.python3Packages.pyusb
              pkgs.libusb1
            ];

            nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
            pythonImportsCheck = [ "ada305" ];

            meta = {
              description = "Safe Linux control for Altec Lansing ADA305 USB speakers";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.linux;
              mainProgram = "ada305";
            };
          };
        in
        {
          inherit ada305;
          default = ada305;
        }
      );

      apps = eachSystem (
        pkgs:
        let
          package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          default = {
            type = "app";
            program = "${package}/bin/ada305";
          };
          ada305 = {
            type = "app";
            program = "${package}/bin/ada305";
          };
        }
      );

      checks = eachSystem (pkgs: {
        ada305 = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });

      devShells = eachSystem (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (
              ps: with ps; [
                pyusb
                pytest
                setuptools
              ]
            ))
            pkgs.libusb1
            pkgs.ruff
            pkgs.usbutils
          ];

          # pyusb loads libusb with ctypes, so it needs the store path on the
          # dynamic loader search path even though Python already knows pyusb.
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libusb1 ];

          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            echo "ada305 dev shell"
            echo "  pytest                  # fake transport only, no device writes"
            echo "  python -m ada305 status # read-only; may need sudo/udev access"
          '';
        };
      });

      formatter = eachSystem (pkgs: pkgs.nixfmt);
    };
}
