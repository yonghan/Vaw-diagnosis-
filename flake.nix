{
  description = "vwdiag - command-line diagnostics for VW Group vehicles (UDS / OBD-II / TP 2.0 over SocketCAN)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      mkPackage = pkgs:
        pkgs.python3Packages.buildPythonApplication {
          pname = "vwdiag";
          version = "0.1.0";
          pyproject = true;
          src = self;
          build-system = [ pkgs.python3Packages.setuptools ];
          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
          pythonImportsCheck = [ "vwdiag" ];
          meta = {
            description = "Command-line diagnostics for VW Group vehicles (UDS / OBD-II / TP 2.0 over SocketCAN)";
            license = pkgs.lib.licenses.mit;
            mainProgram = "vwdiag";
            platforms = pkgs.lib.platforms.linux;
          };
        };
    in
    {
      packages = forAllSystems (pkgs: rec {
        vwdiag = mkPackage pkgs;
        default = vwdiag;
      });

      apps = forAllSystems (pkgs: rec {
        vwdiag = { type = "app"; program = "${self.packages.${pkgs.system}.vwdiag}/bin/vwdiag"; };
        default = vwdiag;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.pytest ]))
            pkgs.can-utils # candump, cansend, slcand, ...
            pkgs.iproute2
          ];
        };
      });

      overlays.default = final: _prev: { vwdiag = mkPackage final; };

      # Usage in a NixOS configuration:
      #   imports = [ inputs.vwdiag.nixosModules.default ];
      #   programs.vwdiag.enable = true;
      nixosModules.default = { config, lib, pkgs, ... }:
        let cfg = config.programs.vwdiag;
        in {
          options.programs.vwdiag = {
            enable = lib.mkEnableOption "vwdiag, VW Group diagnostics CLI";
            package = lib.mkOption {
              type = lib.types.package;
              default = self.packages.${pkgs.system}.vwdiag;
              defaultText = lib.literalExpression "inputs.vwdiag.packages.\${pkgs.system}.vwdiag";
              description = "The vwdiag package to install.";
            };
            canTools = lib.mkEnableOption "can-utils alongside vwdiag" // { default = true; };
          };
          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ cfg.package ] ++ lib.optional cfg.canTools pkgs.can-utils;
            # Bring up SocketCAN drivers for common USB adapters (PEAK, candlelight/gs_usb, slcan).
            boot.kernelModules = [ "can" "can_raw" "vcan" "peak_usb" "gs_usb" "slcan" ];
          };
        };
    };
}
