# /// script
# requires-python = ">=3.12"
# dependencies = ["jsonschema==4.25.1", "pypdf==5.9.0", "ruamel.yaml==0.18.15"]
# ///
"""配布物での iakit の実行時コマンド（D-M4-10）。

  uv run <ia-orchestrator>/scripts/run_iakit.py run init DIR --run-id ID --task-type TYPE --host HOST --model MODEL
  uv run <ia-orchestrator>/scripts/run_iakit.py contracts check DIR

uv がない環境では、依存（jsonschema、pypdf、ruamel.yaml）を入れた Python で python run_iakit.py ... と実行する。
どちらも使えなければ、ia-orchestrator の references/checklist.md で点検し、機械検査を not_run として報告する。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iakit import cli_run, contracts, yamlio  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="run_iakit")
    sub = parser.add_subparsers(dest="group", required=True)
    cli_run.add_parsers(sub)
    check = sub.add_parser("contracts").add_subparsers(dest="action", required=True).add_parser("check")
    check.add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    if args.group == "contracts":
        out = contracts.check_bundle(args.dir)
        sys.stdout.write(yamlio.dumps(out))
        return 0 if out["result"] == "pass" else 1
    return cli_run.command(args)


if __name__ == "__main__":
    sys.exit(main())
