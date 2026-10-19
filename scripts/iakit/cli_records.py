"""iakit decision・iakit finding・iakit question の引数と実行（記録の書き込み、P1-2・P1-3）。"""

import sys
from pathlib import Path

from . import decision_cmd, finding_cmd, question_cmd, yamlio

GROUPS = ("decision", "finding", "question")


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
        else:
            out = question_cmd.answer(args.dir, args.question_id, args.file, args.classification)
    except (ValueError, OSError, yamlio.DataError) as exc:
        sys.stdout.write(yamlio.dumps({"result": "fail", "error": str(exc)}))
        return 1
    sys.stdout.write(yamlio.dumps(out))
    return 0
