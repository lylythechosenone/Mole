{
  description = "Mole - Deep clean and optimize your Mac";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "aarch64-darwin" "x86_64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: import nixpkgs {
        inherit system;
      };
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = pkgsFor system;
        in
        {
          mole = pkgs.buildGoModule {
            pname = "mole";
            version = "1.58.0";
            src = ./.;

            vendorHash = "sha256-TzaadXDCwwu+KBI5Pj/u6hMWKssvyb7zGZYSbkuqT3U=";

            subPackages = [ "cmd/analyze" "cmd/status" ];
            ldflags = [ "-s" "-w" ];
            doCheck = false;

            postInstall = ''
              mkdir -p $out/share/mole/bin $out/share/mole/lib

              # Relocate compiled Go binaries to the expected internal helper paths
              mv $out/bin/analyze $out/share/mole/bin/analyze-go
              mv $out/bin/status $out/share/mole/bin/status-go

              # Copy scripts and library modules
              cp -r bin/*.sh $out/share/mole/bin/
              cp -r lib/* $out/share/mole/lib/
              cp mole mo $out/share/mole/
              chmod +x $out/share/mole/mole $out/share/mole/mo $out/share/mole/bin/*.sh

              # Pin SCRIPT_DIR so mole reliably finds its lib and bin resources regardless of how it is invoked
              substituteInPlace $out/share/mole/mole \
                --replace-fail 'SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"' "SCRIPT_DIR=\"$out/share/mole\""

              # Symlink user-facing binaries into $out/bin
              ln -sf $out/share/mole/mole $out/bin/mole
              ln -sf $out/share/mole/mo $out/bin/mo
            '';

            meta = with pkgs.lib; {
              description = "Deep clean and optimize your Mac";
              homepage = "https://mole.fit";
              license = licenses.gpl3Only;
              platforms = [ "aarch64-darwin" "x86_64-darwin" ];
              mainProgram = "mole";
            };
          };

          default = self.packages.${system}.mole;
        });

      apps = forAllSystems (system: {
        mole = {
          type = "app";
          program = "${self.packages.${system}.mole}/bin/mole";
          meta.description = "Deep clean and optimize your Mac";
        };
        mo = {
          type = "app";
          program = "${self.packages.${system}.mole}/bin/mo";
          meta.description = "Mole CLI alias";
        };
        default = self.apps.${system}.mole;
      });

      devShells = forAllSystems (system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = with pkgs; [
              # Go toolchain and linters
              go
              golangci-lint
              gotools # provides goimports

              # Shell scripting and linting
              bash
              shellcheck
              shfmt

              # Test suites and execution
              bats
              parallel # parallel bats execution

              # Build and development utilities
              gnumake
              coreutils-prefixed # provides gtimeout without shadowing macOS BSD tools
              python3 # required by audit scripts in scripts/check.sh
              fd # optional test dependency (installer_fd.bats)
              zip # optional test dependency (installer_zip.bats)
              unzip
              git
            ];
          };
        });
    };
}
