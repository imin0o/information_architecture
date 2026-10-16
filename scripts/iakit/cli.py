"""iakit コマンドの入口。

  iakit manifest check [--local]   原典登録簿の検査。--local は手元の原典とsha256で照合する
  iakit leaks check [--staged] [--dir PATH ...]   原典・機密データのGit追加と混入の検出
  iakit fixtures build | check     自作素材の生成と一致確認
  iakit check [--local]            上記の検査をまとめて実行する

各検査は --report PATH で本文を含まない結果をYAMLへ保存できる。終了コードは失敗時1。
"""

import argparse
import sys
from pathlib import Path

from . import fixtures, leak_history, leaks, manifest, paths, yamlio


def _emit(results: list[dict], report: Path | None) -> int:
    failed = any(r["result"] == "fail" for r in results)
    text = yamlio.dumps({"schema_version": "0.1.0", "result": "fail" if failed else "pass", "checks": results})
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iakit", description="情報設計Skill群の構築・検査ツール")
    sub = parser.add_subparsers(dest="group", required=True)

    def with_report(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--report", type=Path, help="結果を保存するYAMLのパス（例: library/build-logs/checks/m0.yaml）")
        return p

    m = sub.add_parser("manifest").add_subparsers(dest="action", required=True)
    with_report(m.add_parser("check")).add_argument("--local", action="store_true")

    lk = with_report(sub.add_parser("leaks").add_subparsers(dest="action", required=True).add_parser("check"))
    mode = lk.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="indexのうちコミット予定の差分だけを見る")
    mode.add_argument("--dir", type=Path, action="append", help="Gitではなくディレクトリを検査する（dist/ 等）")
    mode.add_argument("--pre-push", metavar="REMOTE", help="標準入力のpre-push参照情報に従い履歴を検査する")

    fx = sub.add_parser("fixtures").add_subparsers(dest="action", required=True)
    fx.add_parser("build")
    with_report(fx.add_parser("check"))

    with_report(sub.add_parser("check")).add_argument("--local", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        paths.require_checkout()
    except ValueError as exc:
        parser.error(str(exc))
    if args.group == "fixtures" and args.action == "build":
        for name in fixtures.build():
            print(f"wrote {name}")
        return 0
    if args.group == "manifest":
        results = [manifest.check(local=args.local)]
    elif args.group == "leaks":
        results = [leak_history.check_push(sys.stdin.read(), args.pre_push)] if args.pre_push else [
            leaks.check(staged_only=args.staged, dirs=args.dir)
        ]
    elif args.group == "fixtures":
        results = [fixtures.check()]
    else:
        results = [manifest.check(local=args.local), fixtures.check(), leaks.check()]
    return _emit(results, args.report)


if __name__ == "__main__":
    sys.exit(main())
