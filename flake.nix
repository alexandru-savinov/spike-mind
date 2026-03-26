{
  description = "spike-mind — LLM-controlled LEGO SPIKE Prime robot via BLE";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      nixpkgs,
      flake-utils,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3.withPackages (
          ps: with ps; [
            bleak # BLE client — talks to SPIKE hub
          ]
        );
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.bluez # Bluetooth stack
          ];

          shellHook = ''
            echo "spike-mind dev shell"
            echo "  Python: $(python3 --version)"
            echo "  bleak:  $(python3 -c 'import bleak; print(bleak.__version__)')"
          '';
        };
      }
    );
}
