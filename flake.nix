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
        isLinux = pkgs.stdenv.isLinux;
        python = pkgs.python3.withPackages (
          ps: with ps; [
            bleak
            anthropic
            pytest-asyncio
          ]
          ++ pkgs.lib.optionals isLinux [ ps.dbus-next ]
        );
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.python3Packages.pytest
          ]
          ++ pkgs.lib.optionals isLinux [ pkgs.bluez ];

          shellHook = ''
            echo "spike-mind dev shell"
            echo "  Python: $(python3 --version)"
            echo "  bleak:  $(python3 -c 'from importlib.metadata import version; print(version("bleak"))')"
            export PYTHONPATH="$PWD/src:$PYTHONPATH"
          '';
        };
      }
    );
}
