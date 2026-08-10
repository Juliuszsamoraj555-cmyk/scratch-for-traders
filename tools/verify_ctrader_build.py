"""
Real cTrader Automate build check for generated cBots.

Runs every strategy in tests/fixtures/ (or one given file) through
render_csharp(), drops the result into a scratch .NET class-library
project (ctrader_build_check/) that references the real `cTrader.Automate`
NuGet package, and runs `dotnet build` against it - the same .NET SDK
compiler cTrader Automate's own Ctrl+B uses under the hood (cTrader
Desktop 4.2+ builds cBots via the .NET SDK, and officially supports
building via `dotnet build` from outside the app - see
https://help.ctrader.com/ctrader-algo/documentation/visual-studio-ides/).

This is real verification against the real cAlgo API surface - not a
simulation. It is NOT guaranteed 100% identical to what cTrader Automate's
own embedded build does (e.g. its default AlgoBuild/AlgoPublish MSBuild
properties, or which exact package version it pins), but a build failure
here is a build failure, full stop; a clean build here is strong evidence
(not yet a 100% guarantee) it will also build inside the actual app.

Usage:
    python tools/verify_ctrader_build.py                    # all fixtures
    python tools/verify_ctrader_build.py --fixture simple_single
    python tools/verify_ctrader_build.py --file path/to/strategy.json
    python tools/verify_ctrader_build.py --matrix            # tools/strategy_matrix.py (many combinations)

Requires the .NET SDK (`dotnet` on PATH) and network access to nuget.org
the first time (to restore the cTrader.Automate package - cached after).

On any failure, the failing strategy's JSON is written to
tests/fixtures/failures/<name>.json so it can be inspected, re-run in
isolation (--file), and turned into a permanent regression fixture once
fixed.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Force English dotnet/MSBuild output regardless of the machine's locale -
# both for reliable "error"/"warning" text matching below, and because the
# default locale's output encoding (e.g. Polish cp1250) isn't guaranteed to
# be decodable as UTF-8, which crashed subprocess's output-reading thread
# the first time this ran with diacritics in the localized text.
_DOTNET_ENV = {**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en"}

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import WorkspaceConfig, parse_strategy, render_csharp  # noqa: E402
from tools.strategy_matrix import generate_matrix  # noqa: E402

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
FAILURES_DIR = FIXTURES_DIR / "failures"
PROJECT_DIR = Path(__file__).parent.parent / "ctrader_build_check"
CS_FILE = PROJECT_DIR / "ScratchForTradersBot.cs"
CSPROJ_FILE = PROJECT_DIR / "ScratchForTradersBot.csproj"


def ensure_project():
    """Scaffold the scratch build project once if it doesn't exist yet -
    see HANDOFF.md for why this shape (a plain classlib referencing
    cTrader.Automate) is the right one."""
    if CSPROJ_FILE.exists():
        return
    PROJECT_DIR.mkdir(exist_ok=True)
    subprocess.run(
        ["dotnet", "new", "classlib", "--name", "ScratchForTradersBot", "-o", str(PROJECT_DIR), "--force"],
        check=True, capture_output=True, env=_DOTNET_ENV,
    )
    stub = PROJECT_DIR / "Class1.cs"
    stub.unlink(missing_ok=True)
    subprocess.run(
        ["dotnet", "add", str(CSPROJ_FILE), "package", "cTrader.Automate"],
        check=True, capture_output=True, env=_DOTNET_ENV,
    )


def build_one(name: str, config: WorkspaceConfig, config_dict: dict) -> bool:
    ir = parse_strategy(config)
    CS_FILE.write_text(render_csharp(ir), encoding="utf-8")

    result = subprocess.run(
        ["dotnet", "build", "--configuration", "Release"],
        cwd=str(PROJECT_DIR), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=_DOTNET_ENV,
    )
    success = result.returncode == 0
    error_lines = [line for line in result.stdout.splitlines() if ": error " in line]
    warning_count = sum(1 for line in result.stdout.splitlines() if ": warning " in line)

    status = "PASS" if success else "FAIL"
    print(f"  {status}  {name}  ({warning_count} warnings)")
    if not success:
        for line in error_lines or result.stdout.splitlines()[-15:]:
            print(f"        {line}")
        FAILURES_DIR.mkdir(parents=True, exist_ok=True)
        failure_path = FAILURES_DIR / f"{name}.json"
        failure_path.write_text(json.dumps(config_dict, indent=2), encoding="utf-8")
        print(f"        reproducer saved to {failure_path} - re-run with --file \"{failure_path}\"")
    return success


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture", help="Run only tests/fixtures/<name>.json")
    parser.add_argument("--file", help="Run a specific strategy JSON file instead of the fixtures")
    parser.add_argument("--matrix", action="store_true", help="Run the full combinatorial matrix from tools/strategy_matrix.py instead of tests/fixtures/")
    args = parser.parse_args()

    try:
        ensure_project()
    except subprocess.CalledProcessError as e:
        print("Failed to set up the build-check project (is `dotnet` on PATH? is nuget.org reachable?):")
        print(e.stderr.decode(errors="replace") if e.stderr else e)
        sys.exit(2)

    if args.matrix:
        named_configs = generate_matrix()
    elif args.file:
        with open(args.file, encoding="utf-8") as f:
            named_configs = [(Path(args.file).stem, json.load(f))]
    elif args.fixture:
        with open(FIXTURES_DIR / f"{args.fixture}.json", encoding="utf-8") as f:
            named_configs = [(args.fixture, json.load(f))]
    else:
        named_configs = []
        for p in sorted(FIXTURES_DIR.glob("*.json")):
            with open(p, encoding="utf-8") as f:
                named_configs.append((p.stem, json.load(f)))

    print(f"Running {len(named_configs)} strategies...\n")
    results = []
    for name, config_dict in named_configs:
        config = WorkspaceConfig(**config_dict)
        results.append(build_one(name, config, config_dict))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} strategies built successfully.")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
