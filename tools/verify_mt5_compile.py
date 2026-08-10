"""
Real MetaEditor compile check for generated .mq5 strategies.

Runs every strategy in tests/fixtures/ (or one given file) through
render_mql5(), drops the result into a real MT5 Data Folder's MQL5\\Experts
directory, and invokes MetaEditor64.exe's command-line compiler
(`/compile:`) against it - the exact same compiler MetaEditor's own F7
button uses, just non-interactive. This is real verification, not a
simulation: if MetaEditor rejects the code, this fails; if it produces a
fresh .ex5, it didn't.

Why "does a fresh .ex5 exist" instead of parsing the /log output: in this
environment MetaEditor's /log file was unreliable (either not written, or
written in an encoding this script couldn't reliably decode - possibly a
quirk of running it non-interactively rather than from its own GUI). The
.ex5 file is the ground truth anyway: MetaEditor only ever produces one on
a successful compile, so deleting any stale one first and checking for a
freshly-created one is a robust, encoding-independent success signal.

Usage:
    python tools/verify_mt5_compile.py                    # all fixtures
    python tools/verify_mt5_compile.py --fixture simple_single
    python tools/verify_mt5_compile.py --file path/to/strategy.json
    python tools/verify_mt5_compile.py --matrix            # tools/strategy_matrix.py (many combinations)

On any failure, the failing strategy's JSON is written to
tests/fixtures/failures/<name>.json so it can be inspected, re-run in
isolation (--file), and turned into a permanent regression fixture once
fixed.

Auto-discovers MetaEditor64.exe under Program Files, and the first MT5
Data Folder under %APPDATA%\\MetaQuotes\\Terminal\\*\\ that has an
MQL5\\Experts directory (i.e. has actually been run at least once - a
fresh/never-launched install has no Data Folder yet, which is exactly the
"no MQL5\\Include available to compile against" situation this script
cannot work around). Override either with --metaeditor / --data-folder if
auto-discovery picks the wrong one (e.g. multiple brokers installed).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import WorkspaceConfig, parse_strategy, render_mql5  # noqa: E402
from tools.strategy_matrix import generate_matrix  # noqa: E402

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
FAILURES_DIR = FIXTURES_DIR / "failures"
TEST_FILENAME = "scratch_for_traders_verify.mq5"


def find_metaeditor() -> "Path | None":
    candidates = []
    for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
        base_path = Path(base)
        if base_path.exists():
            candidates.extend(base_path.glob("*/MetaEditor64.exe"))
    return candidates[0] if candidates else None


def find_data_folder() -> "Path | None":
    root = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not root.exists():
        return None
    for terminal_dir in root.iterdir():
        experts = terminal_dir / "MQL5" / "Experts"
        if experts.is_dir():
            return terminal_dir
    return None


def compile_one(name: str, config: WorkspaceConfig, config_dict: dict, metaeditor: Path, experts_dir: Path) -> bool:
    ir = parse_strategy(config)
    mql5_code = render_mql5(ir)

    mq5_path = experts_dir / TEST_FILENAME
    ex5_path = mq5_path.with_suffix(".ex5")

    mq5_path.write_text(mql5_code, encoding="utf-8")
    if ex5_path.exists():
        ex5_path.unlink()

    # subprocess.run with an argument list (not a shell string) sidesteps
    # cmd.exe's quoting rules entirely - the classic "'C:\Program' is not
    # recognized" failure mode of building this as one os.system() string.
    subprocess.run([str(metaeditor), f"/compile:{mq5_path}"], capture_output=True)
    # MetaEditor runs asynchronously - give it a moment to finish.
    for _ in range(20):
        if ex5_path.exists():
            break
        time.sleep(0.5)

    success = ex5_path.exists() and ex5_path.stat().st_size > 0
    size = ex5_path.stat().st_size if ex5_path.exists() else 0

    mq5_path.unlink(missing_ok=True)
    ex5_path.unlink(missing_ok=True)

    print(f"  {'PASS' if success else 'FAIL'}  {name}  ({size} bytes)" if success else f"  FAIL  {name}  (no .ex5 produced)")
    if not success:
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
    parser.add_argument("--metaeditor", help="Path to MetaEditor64.exe (auto-discovered if omitted)")
    parser.add_argument("--data-folder", help="Path to the MT5 Data Folder (auto-discovered if omitted)")
    args = parser.parse_args()

    metaeditor = Path(args.metaeditor) if args.metaeditor else find_metaeditor()
    if not metaeditor or not metaeditor.exists():
        print("Could not find MetaEditor64.exe. Pass --metaeditor <path> explicitly.")
        sys.exit(2)

    data_folder = Path(args.data_folder) if args.data_folder else find_data_folder()
    if not data_folder:
        print("Could not find an initialized MT5 Data Folder (needs MQL5\\Include\\Trade\\Trade.mqh etc.")
        print("to actually exist - i.e. the terminal must have been launched at least once).")
        print("Pass --data-folder <path> explicitly, or launch the MT5 terminal once first.")
        sys.exit(2)
    experts_dir = data_folder / "MQL5" / "Experts"

    print(f"MetaEditor: {metaeditor}")
    print(f"Data Folder: {data_folder}")
    print()

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
        results.append(compile_one(name, config, config_dict, metaeditor, experts_dir))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} strategies compiled successfully.")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
