"""
One-time CmdStan install into Prophet's bundled stan_model directory.
Called by `make install`. Safe to re-run — skips if already present.
"""
import pathlib
import sys

try:
    import prophet
    import cmdstanpy
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

stan_dir = pathlib.Path(prophet.__file__).parent / "stan_model"
stan_dir.mkdir(parents=True, exist_ok=True)
target = stan_dir / "cmdstan-2.33.1"

if (target / "Makefile").exists():
    print("CmdStan 2.33.1 already present, skipping.")
else:
    print("Downloading + compiling CmdStan 2.33.1 (~400 MB, takes a few minutes)...")
    cmdstanpy.install_cmdstan(dir=str(stan_dir), version="2.33.1")
    print("CmdStan 2.33.1 installed.")
