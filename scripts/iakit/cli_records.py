"""iakit decision・finding・question・simulation・measure・analytics の引数と実行（記録の書き込み、P1〜P4）。"""

import sys
from pathlib import Path

from . import analytics_cmd, decision_cmd, finding_cmd, measure, question_cmd, simulation_cmd, yamlio

GROUPS = ("decision", "finding", "question", "simulation", "measure", "analytics")


def _data_args(p) -> None:
    p.add_argument("source", nargs="?", help="JSONまたはYAMLのファイル。- で標準入力")
    p.add_argument("--json", help="JSONを引数で渡す")


def add_parsers(sub) -> None:
    dc = sub.add_parser("decision").add_subparsers(dest="action", required=True)
    da = dc.add_parser("add", help="判断を追加する。ID・版・記録日時・照合状態・confidence・機密区分は補う")
    da.add_argument("dir", type=Path)
    _data_args(da)
    dr = dc.add_parser("revise", help="最新版に変更を当てて版を上げ、旧版を superseded にする")
    dr.add_argument("dir", type=Path)
    dr.add_argument("decision_id", metavar="IA-nnn")
    _data_args(dr)
    dw = dc.add_parser("withdraw", help="最新版を withdrawn にする")
    dw.add_argument("dir", type=Path)
    dw.add_argument("decision_id", metavar="IA-nnn")
    fc = sub.add_parser("finding").add_subparsers(dest="action", required=True)
    fa = fc.add_parser("add", help="指摘を findings.yaml に追加する")
    fa.add_argument("dir", type=Path)
    _data_args(fa)
    fa.add_argument("--rubric-version", help="findings.yaml の rubric_version")
    fr = fc.add_parser("resolve", help="指摘の状態を変える。resolved は解消を確かめた版を記録する")
    fr.add_argument("dir", type=Path)
    fr.add_argument("finding_id", metavar="FIND-nnn")
    fr.add_argument("--status", default="resolved", choices=["resolved", "open", "unresolved", "accepted"])
    fr.add_argument("--version", type=int, help="解消を確かめた対象の版（省略時は現行版）")
    fr.add_argument("--note")
    qc = sub.add_parser("question").add_subparsers(dest="action", required=True)
    qa = qc.add_parser("answer", help="回答を hearing_answer として登録し、影響する判断と成果物を示す")
    qa.add_argument("dir", type=Path)
    qa.add_argument("question_id", metavar="Q-nnn")
    qa.add_argument("file", type=Path)
    qa.add_argument("--classification", choices=["public", "internal", "confidential"])
    sc = sub.add_parser("simulation").add_subparsers(dest="action", required=True)
    sk = sc.add_parser("kit", help="模擬評価の評価者に渡す材料（ラベルの階層・カード）を出す。設計の文脈は含めない")
    sk.add_argument("dir", type=Path)
    sk.add_argument("--method", choices=["tree_test", "closed_card_sort"], required=True)
    sa = sc.add_parser("add", help="試行の記録から到達率を導いて simulations.yaml に追加する")
    sa.add_argument("dir", type=Path)
    _data_args(sa)
    ai = sub.add_parser("analytics").add_subparsers(dest="action", required=True).add_parser(
        "import", help="解析データのCSVエクスポートを登録し、構成比を基準と比べた調査記録を observations.yaml に追加する")
    ai.add_argument("dir", type=Path)
    ai.add_argument("file", type=Path)
    ai.add_argument("--source", required=True, choices=["ga4", "search_console", "other"])
    ai.add_argument("--aspect", required=True, choices=["traffic_source", "device", "page_transition", "other"])
    ai.add_argument("--description", required=True)
    ai.add_argument("--period-start", required=True, metavar="YYYY-MM-DD")
    ai.add_argument("--period-end", required=True, metavar="YYYY-MM-DD")
    ai.add_argument("--target", required=True, metavar="ART-nnn", help="検証する判断の対象成果物（現行版を記録する）")
    ai.add_argument("--dimension", required=True, help="値を数える列")
    ai.add_argument("--metric", required=True, help="指標の列（セッション数・クリック数など）")
    ai.add_argument("--value", required=True, help="構成比を測る次元の値")
    ai.add_argument("--filter", nargs="*", default=[], metavar="列=値", help="母集団を絞る条件（ページ遷移の遷移元など）")
    ai.add_argument("--op", required=True, choices=["ge", "le"], help="ge: 構成比が基準以上で supported")
    ai.add_argument("--threshold", required=True, type=float, help="基準の構成比（0〜1）")
    ai.add_argument("--min-total", type=float, default=analytics_cmd.DEFAULT_MIN_TOTAL,
                    help="母数がこれ未満なら inconclusive")
    ai.add_argument("--classification", choices=["public", "internal", "confidential"])
    ms = sub.add_parser("measure").add_subparsers(dest="action", required=True).add_parser(
        "structure", help="サイトマップ（とビルド済みサイト）の導線を測り、measurements/structure.yaml に書く")
    ms.add_argument("dir", type=Path)
    ms.add_argument("--site", type=Path, help="ビルド済みサイトのディレクトリ（index.html を含む）")
    ms.add_argument("--baseline", type=Path, help="比較する変更前のビルド済みサイト")
    ms.add_argument("--max-clicks", type=int, default=3, help="到達先までのクリック数の基準")


def _data(args) -> dict:
    if (args.source is None) == (args.json is None):
        raise ValueError("ファイル（- で標準入力）か --json のどちらか一方で内容を渡す")
    text = args.json if args.json is not None else (
        sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8"))
    data = yamlio.loads(text)
    if not isinstance(data, dict):
        raise ValueError("内容は1件分のオブジェクトにする")
    return data


def command(args) -> int:
    try:
        if args.group == "decision" and args.action == "add":
            out = decision_cmd.add(args.dir, _data(args))
        elif args.group == "decision" and args.action == "revise":
            out = decision_cmd.revise(args.dir, args.decision_id, _data(args))
        elif args.group == "decision":
            out = decision_cmd.withdraw(args.dir, args.decision_id)
        elif args.group == "finding" and args.action == "add":
            out = finding_cmd.add(args.dir, _data(args), args.rubric_version)
        elif args.group == "finding":
            out = finding_cmd.resolve(args.dir, args.finding_id, args.status, args.version, args.note)
        elif args.group == "question":
            out = question_cmd.answer(args.dir, args.question_id, args.file, args.classification)
        elif args.group == "simulation":
            out = (simulation_cmd.kit(args.dir, args.method) if args.action == "kit"
                   else simulation_cmd.add(args.dir, _data(args)))
        elif args.group == "analytics":
            filters = dict(f.split("=", 1) for f in args.filter if "=" in f)
            if len(filters) != len(args.filter):
                raise ValueError("--filter は 列=値 の形で指定する")
            out = analytics_cmd.import_export(args.dir, args.file, {
                "source": args.source, "aspect": args.aspect, "description": args.description,
                "period_start": args.period_start, "period_end": args.period_end, "target": args.target,
                "dimension": args.dimension, "metric": args.metric, "value": args.value, "filters": filters,
                "op": args.op, "threshold": args.threshold, "min_total": args.min_total,
                "classification": args.classification})
        else:
            out = measure.structure(args.dir, args.site, args.baseline, args.max_clicks)
    except (ValueError, OSError, yamlio.DataError) as exc:
        sys.stdout.write(yamlio.dumps({"result": "fail", "error": str(exc)}))
        return 1
    sys.stdout.write(yamlio.dumps(out))
    return 0
