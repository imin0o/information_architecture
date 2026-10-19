"""iakit コマンドの入口。

  iakit manifest check [--local]   原典登録簿の検査。--local は手元の原典とsha256で照合する
  iakit leaks check [--staged] [--dir PATH ...]   原典・機密データのGit追加と混入の検出
  iakit fixtures build | check     自作素材の生成と一致確認
  iakit check [--local]            上記の検査をまとめて実行する
  iakit extract run (--book ID | --fixture PATH) [--root DIR]   固定抽出スナップショットを作る
  iakit extract check [--local] [DIR ...]   スナップショットの整合。--local は原典ハッシュも照合する
  iakit excerpt ref --extraction DIR --lines A-B   抜粋範囲のハッシュと位置の候補（本文は出さない）
  iakit hosts probe --host claude|codex [--space-in-path]   互換性確認用Skillの発見・明示利用・付属資料参照
  iakit contracts check [--local] [DIR ...]   実行記録・判断・出典・構造のスキーマと整合性。--local は抜粋を固定テキストと照合する
  iakit cases check                課題ケースとルーブリックの固定状態（入力ハッシュ・項目の網羅・検査の実在）
  iakit library check              知識の索引・用語・矛盾・引用台帳・生成記録の整合と、主張レビューから導く採用状態
  iakit run init DIR --run-id ID --task-type TYPE --host HOST --model MODEL   実行ディレクトリと経路・Skillの記録を作る
  iakit input add DIR FILE --kind KIND --description TEXT [--format F] [--classification C] [--replace INPUT-nnn]
                                   入力を固定・登録し、位置付きの取込み結果 ingest/<ID>.yaml を作る
  iakit run artifact DIR PATH --kind KIND [--depends ID ...] [--decision IA-nnn:REV ...] [--no-impact 理由]
                                   成果物のハッシュ・版・依存・機密区分を登録し、本体の版・区分と依存先の参照版を書き換える
                                   （依存先が変われば後続をstaleにする。--no-impact は影響なしの確認を判断として記録する）
  iakit decision add|revise|withdraw DIR [IA-nnn] [FILE|- | --json TEXT]
                                   判断を追加・改訂・撤回する。導出値を補い、契約検査に新しい誤りが出れば書き込まない
  iakit finding add DIR [FILE|- | --json TEXT] | resolve DIR FIND-nnn [--status S] [--version N]
                                   評価の指摘を findings.yaml に追加・更新する（登録は run artifact）
  iakit question answer DIR Q-nnn FILE   回答を hearing_answer として登録し、影響する判断と成果物を示す
  iakit simulation kit DIR --method tree_test|closed_card_sort   模擬評価の評価者に渡す材料（設計の文脈を含めない）
  iakit simulation add DIR [FILE|- | --json TEXT]   模擬評価の試行を記録し、到達率を導いて simulations.yaml に追加する
  iakit measure structure DIR [--site DIR] [--baseline DIR] [--max-clicks N]
                                   サイトマップとビルド済みサイトの導線を測り、指摘の候補を measurements/structure.yaml に書く
  iakit run knowledge DIR KN-nnn ...   段階で読んだ知識本文の内容ハッシュを記録する
  iakit run refresh DIR            入力登録簿のハッシュ・判断参照・未解決の重大な指摘を同期し、依存が変わった成果物をstaleにする
  iakit run event DIR EVENT [--note TEXT] [--status S]   履歴を追加する。resumed は再開前の照合に通った場合だけ
  iakit run resume DIR             再開前の照合（入力登録簿・入力の実体・取込み結果・成果物のハッシュ）
  iakit run judgment DIR --case NAME   独立した判定エージェントのルーブリック判定（rubric-judgment.yaml）を検査する
  iakit dist build [--out DIR]     配布物を新しいディレクトリで生成・検査し、dist/ を丸ごと入れ替える
  iakit dist check [DIR]           DIR を指定すれば配置済み配布物の検査。省略時は再生成して verification/distributions.yaml と照合
  iakit dist diff OLD NEW          2つの配布ディレクトリの配布IDとファイルの差分
  iakit accept host --host claude|codex [--scenario NAME ...]   ホストのモデルで配置先の配布物を使う受け入れ検査（M6）
  iakit accept route --host H --case NAME --run-id ID [--refine]   代表ケースを配置先で最後まで実行し、runs/ へ回収する（M6）
  iakit accept placement           dist/ をビルド元の外へ両ホストの配置先として写し、同梱の実行時処理で受け入れ検査する（M6）
  iakit review sheet               人の確認用シートを sources/review/ に作る（Git管理外。本文を含む）
  iakit review record --by NAME (--all-ok | --reject EV | --claim CLAIM=STATUS | --sample-issue ID:KIND:MEMO)
                                   人の判定から主張レビュー・照合記録・抽出品質の確認を書き込む

各検査は --report PATH で本文を含まない結果をYAMLへ保存できる。終了コードは失敗時1。
"""

import argparse
import sys
from pathlib import Path

from . import (accept, accept_host, accept_route, cases, cli_records, cli_run, contracts, dist, extract_cmd, fixtures, hostprobe, leak_history, leaks, library,
               manifest, paths, push_check, review, yamlio)


def _emit(results: list[dict], report: Path | None) -> int:
    failed = any(r["result"] in ("fail", "blocked") for r in results)
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

    ex = sub.add_parser("extract").add_subparsers(dest="action", required=True)
    run = with_report(ex.add_parser("run"))
    src = run.add_mutually_exclusive_group(required=True)
    src.add_argument("--book", help="登録簿のBOOK-ID。sha256が一致する原典を sources/originals/ から探す")
    src.add_argument("--fixture", help="tests/fixtures/ からの相対パス（例: pdf/page-labels.pdf）")
    run.add_argument("--root", type=Path, help="保存先（既定: sources/extracted）")
    exc = with_report(ex.add_parser("check"))
    exc.add_argument("--local", action="store_true")
    exc.add_argument("dirs", type=Path, nargs="*")

    er = with_report(sub.add_parser("excerpt").add_subparsers(dest="action", required=True).add_parser("ref"))
    er.add_argument("--extraction", type=Path, required=True)
    er.add_argument("--lines", required=True, help="1始まり・両端を含む行範囲（例: 12-15）")

    hp = with_report(sub.add_parser("hosts").add_subparsers(dest="action", required=True).add_parser("probe"))
    hp.add_argument("--host", choices=sorted(hostprobe.HOSTS), action="append", required=True)
    hp.add_argument("--space-in-path", action="store_true")

    ct = with_report(sub.add_parser("contracts").add_subparsers(dest="action", required=True).add_parser("check"))
    ct.add_argument("--local", action="store_true")
    ct.add_argument("--extracted", type=Path, help="固定スナップショットの場所（既定: sources/extracted）")
    ct.add_argument("dirs", type=Path, nargs="*")

    with_report(sub.add_parser("cases").add_subparsers(dest="action", required=True).add_parser("check"))
    with_report(sub.add_parser("library").add_subparsers(dest="action", required=True).add_parser("check"))
    rv = sub.add_parser("review").add_subparsers(dest="action", required=True)
    with_report(rv.add_parser("sheet"))
    rr = with_report(rv.add_parser("record"))
    rr.add_argument("--by", required=True, help="確認した人の名前")
    rr.add_argument("--all-ok", action="store_true", help="指定しなかった箇所を、位置一致・supported・問題なしとして記録する")
    rr.add_argument("--reject", action="append", default=[], metavar="EV-nnnn", help="原典の位置で抜粋が見つからない")
    rr.add_argument("--claim", action="append", default=[], metavar="CLAIM-nnnn=STATUS",
                    help="supported・not_supported・unclear")
    rr.add_argument("--sample-issue", action="append", default=[], metavar="EXTRACT-ID:KIND:MEMO",
                    help="抽出品質の問題。メモに原典の本文を書かない")

    ds = sub.add_parser("dist").add_subparsers(dest="action", required=True)
    with_report(ds.add_parser("build")).add_argument("--out", type=Path, help="出力先（既定: dist）")
    with_report(ds.add_parser("check")).add_argument("dir", type=Path, nargs="?")
    dd = with_report(ds.add_parser("diff"))
    dd.add_argument("old", type=Path)
    dd.add_argument("new", type=Path)

    ac = sub.add_parser("accept").add_subparsers(dest="action", required=True)
    with_report(ac.add_parser("placement"))
    ah = with_report(ac.add_parser("host"))
    ah.add_argument("--host", choices=sorted(accept_host.COMMANDS), required=True)
    ah.add_argument("--scenario", choices=accept_host.SCENARIOS, action="append")
    ah.add_argument("--repeat", type=int, default=1, help="各シナリオの試行回数")
    ar = with_report(ac.add_parser("route"))
    ar.add_argument("--host", choices=sorted(accept_host.COMMANDS), required=True)
    ar.add_argument("--case", choices=sorted(accept_route.REQUESTS), required=True)
    ar.add_argument("--run-id", required=True)
    ar.add_argument("--refine", action="store_true", help="fail の独立判定を受けた実行を配置先へ戻してRefineさせる")

    cli_run.add_parsers(sub)
    cli_records.add_parsers(sub)
    ck = with_report(sub.add_parser("check")).add_mutually_exclusive_group()
    ck.add_argument("--local", action="store_true")
    ck.add_argument("--pre-push", action="store_true", help="標準入力の送信予定コミットを一時checkoutで全件検査する")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        paths.require_checkout()
    except ValueError as exc:
        parser.error(str(exc))
    if args.group in ("run", "input"):
        return cli_run.command(args)
    if args.group in cli_records.GROUPS:
        return cli_records.command(args)
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
    elif args.group == "extract":
        try:
            results = [extract_cmd.run(args.book, args.fixture, args.root) if args.action == "run"
                       else extract_cmd.check(args.dirs, args.local)]
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    elif args.group == "excerpt":
        try:
            results = [extract_cmd.ref(args.extraction, args.lines)]
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    elif args.group == "hosts":
        results = [hostprobe.probe(h, space_in_path=args.space_in_path) for h in args.host]
    elif args.group == "contracts":
        results = [contracts.check_bundle(d, args.local, args.extracted or paths.EXTRACTED)
                   for d in args.dirs or contracts.default_targets()]
    elif args.group == "cases":
        results = [cases.check()]
    elif args.group == "library":
        results = [library.check()]
    elif args.group == "dist":
        try:
            results = [dist.build(args.out or dist.DIST) if args.action == "build" else
                       dist.check(args.dir) if args.action == "check" else
                       {"check": "dist_diff", "result": "pass", "diff": dist.diff(args.old, args.new)}]
        except (ValueError, OSError) as exc:
            results = [{"check": f"dist_{args.action}", "result": "fail", "errors": [str(exc)]}]
    elif args.group == "accept":
        results = [accept.placement() if args.action == "placement" else
                   accept_host.run(args.host, args.scenario, args.repeat) if args.action == "host" else
                   accept_route.route(args.host, args.case, args.run_id, args.refine)]
    elif args.group == "review":
        results = [review.sheet() if args.action == "sheet" else
                   review.record(args.by, args.all_ok, args.reject, args.claim, args.sample_issue)]
    elif args.pre_push:
        results = [push_check.check_push(sys.stdin.read())]
    else:
        results = [manifest.check(local=args.local), fixtures.check(), leaks.check(), cases.check()]
        results += [contracts.check_bundle(d) for d in contracts.default_targets()]
        results += [library.check()] if (paths.ROOT / library.FILES["index"][0]).is_file() else []
        results += [dist.check()]
    return _emit(results, args.report)


if __name__ == "__main__":
    sys.exit(main())
