"""
Real MetaEditor compile check for generated .mq4 strategies.

Same approach as verify_mt5_compile.py (see its docstring for the full
rationale) - since MetaEditor build 600+ is shared between MT4 and MT5 and
auto-detects the compiler from the file extension, this is almost the same
script, just pointed at an MQL4\\Experts folder and .mq4/.ex4 instead of
MQL5\\Experts and .mq5/.ex5.

Usage:
    python tools/verify_mt4_compile.py                    # all fixtures
    python tools/verify_mt4_compile.py --fixture simple_single
    python tools/verify_mt4_compile.py --file path/to/strategy.json
    python tools/verify_mt4_compile.py --matrix            # tools/strategy_matrix.py (many combinations)

Auto-discovers metaeditor.exe/metaeditor64.exe under Program Files, and the
first MT4 Data Folder under %APPDATA%\\MetaQuotes\\Terminal\\*\\ that has an
MQL4\\Experts directory (i.e. an MT4 terminal that has actually been
launched at least once - a fresh/never-launched install has no Data Folder
yet). Override with --metaeditor / --data-folder if auto-discovery picks
the wrong one.

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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import WorkspaceConfig, parse_strategy, render_mql4  # noqa: E402
from tools.strategy_matrix import generate_matrix  # noqa: E402

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
FAILURES_DIR = FIXTURES_DIR / "failures"
TEST_FILENAME = "scratch_for_traders_verify.mq4"


def find_metaeditor() -> "Path | None":
    """Prefers an install whose folder name suggests MT4 (classic
    `metaeditor.exe`, non-"64" builds are typically MT4-bundled) - an MT5
    install's MetaEditor64.exe *should* also handle .mq4 per the shared-
    compiler unification, but empirically failed silently (no .ex4, no
    error) when pointed at a file outside its own MQL5 Data Folder tree in
    this environment. Using the matching MT4 install's own MetaEditor
    against its own Data Folder is what's actually confirmed to work."""
    candidates = []
    fallback = []
    for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
        base_path = Path(base)
        if not base_path.exists():
            continue
        for exe in base_path.glob("*/metaeditor*.exe"):
            if "4" in exe.parent.name and "64" not in exe.name:
                candidates.append(exe)
            else:
                fallback.append(exe)
    return (candidates or fallback or [None])[0]


def find_data_folder() -> "Path | None":
    root = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not root.exists():
        return None
    for terminal_dir in root.iterdir():
        experts = terminal_dir / "MQL4" / "Experts"
        if experts.is_dir():
            return terminal_dir
    return None


def compile_one(name: str, config: WorkspaceConfig, config_dict: dict, metaeditor: Path, experts_dir: Path) -> bool:
    ir = parse_strategy(config)
    mql4_code = render_mql4(ir)

    mq4_path = experts_dir / TEST_FILENAME
    ex4_path = mq4_path.with_suffix(".ex4")

    mq4_path.write_text(mql4_code, encoding="utf-8")
    if ex4_path.exists():
        ex4_path.unlink()

    subprocess.run([str(metaeditor), f"/compile:{mq4_path}"], capture_output=True)
    for _ in range(20):
        if ex4_path.exists():
            break
        time.sleep(0.5)

    success = ex4_path.exists() and ex4_path.stat().st_size > 0
    size = ex4_path.stat().st_size if ex4_path.exists() else 0

    mq4_path.unlink(missing_ok=True)
    ex4_path.unlink(missing_ok=True)

    print(f"  {'PASS' if success else 'FAIL'}  {name}  ({size} bytes)" if success else f"  FAIL  {name}  (no .ex4 produced)")
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
    parser.add_argument("--metaeditor", help="Path to metaeditor.exe/metaeditor64.exe (auto-discovered if omitted)")
    parser.add_argument("--data-folder", help="Path to the MT4 Data Folder (auto-discovered if omitted)")
    args = parser.parse_args()

    metaeditor = Path(args.metaeditor) if args.metaeditor else find_metaeditor()
    if not metaeditor or not metaeditor.exists():
        print("Could not find metaeditor.exe/metaeditor64.exe. Pass --metaeditor <path> explicitly.")
        sys.exit(2)

    data_folder = Path(args.data_folder) if args.data_folder else find_data_folder()
    if not data_folder:
        print("Could not find an initialized MT4 Data Folder (needs MQL4\\Experts to exist -")
        print("i.e. an MT4 terminal must have been launched at least once).")
        print("Pass --data-folder <path> explicitly, or launch an MT4 terminal once first.")
        sys.exit(2)
    experts_dir = data_folder / "MQL4" / "Experts"

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
