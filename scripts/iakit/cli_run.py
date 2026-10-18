"""iakit run・iakit input の引数と実行（実行ディレクトリの管理）。"""

import sys
from pathlib import Path

from . import contracts, ingest, judgment, run_cmd, run_compare, run_inputs, runs, yamlio


def add_parsers(sub) -> None:
    rn = sub.add_parser("run").add_subparsers(dest="action", required=True)
    ri = rn.add_parser("init")
    ri.add_argument("dir", type=Path)
    ri.add_argument("--run-id", required=True)
    ri.add_argument("--task-type", required=True, choices=list(runs.ROUTES) + list(run_cmd.LATER_ROUTES))
    ri.add_argument("--host", required=True, choices=["claude-code", "codex", "other"])
    ri.add_argument("--host-version")
    ri.add_argument("--model", required=True)
    ri.add_argument("--model-version")
    ra = rn.add_parser("artifact")
    ra.add_argument("dir", type=Path)
    ra.add_argument("path", help="実行ディレクトリからの相対パス")
    ra.add_argument("--kind", required=True)
    ra.add_argument("--depends", nargs="*", default=[], help="本体から読み取れない依存（INPUT-nnn・ART-nnn）")
    ra.add_argument("--decision", nargs="*", default=[], metavar="IA-nnn:REV")
    rk = rn.add_parser("knowledge")
    rk.add_argument("dir", type=Path)
    rk.add_argument("ids", nargs="+", metavar="KN-nnn")
    for name in ("refresh", "resume"):
        rn.add_parser(name).add_argument("dir", type=Path)
    rj = rn.add_parser("judgment", help="独立した判定エージェントによるルーブリック判定の記録を検査する")
    rj.add_argument("dir", type=Path)
    rj.add_argument("--case", required=True, help="tests/cases のケース名（webia-001 など）またはケースのディレクトリ")
    rc = rn.add_parser("compare", help="2つの実行の判断・Skill・知識の差分（NFR-02）")
    rc.add_argument("dir", type=Path, help="基準の実行ディレクトリ")
    rc.add_argument("other", type=Path)
    re_ = rn.add_parser("event")
    re_.add_argument("dir", type=Path)
    re_.add_argument("event", choices=["interrupted", "resumed", "rerun", "refine", "completed"])
    re_.add_argument("--note", default="")
    re_.add_argument("--status", choices=["in_progress", "interrupted", "completed", "unresolved"])
    ia = sub.add_parser("input").add_subparsers(dest="action", required=True).add_parser("add")
    ia.add_argument("dir", type=Path)
    ia.add_argument("file", type=Path)
    ia.add_argument("--kind", required=True, choices=["brief", "hearing_answer", "document", "page_list", "sitemap",
                                                     "cms_export", "taxonomy", "vocabulary", "research_record"])
    ia.add_argument("--description", required=True)
    ia.add_argument("--format", choices=sorted(set(ingest.FORMATS.values())))
    ia.add_argument("--classification", choices=["public", "internal", "confidential"],
                    help="ユーザーが明示した区分。省略時は internal（既定値として記録）")
    ia.add_argument("--replace", metavar="INPUT-nnn", help="既存の入力の実体を差し替える")
    ia.add_argument("--name", help="保存するファイル名（同名の別ファイルがある場合）")


def command(args) -> int:
    """run・input の各操作。成功時は結果を、失敗時はエラーを YAML で出す。"""
    try:
        if args.group == "input":
            out = run_inputs.add(args.dir, args.file, args.kind, args.description, args.format, args.classification,
                                 args.replace, args.name)
        elif args.action == "init":
            out = run_cmd.init(args.dir, args.run_id, args.task_type, args.host, args.model, args.host_version,
                               args.model_version)
        elif args.action == "artifact":
            out = run_cmd.artifact(args.dir, args.path, args.kind, args.depends, args.decision)
        elif args.action == "knowledge":
            out = run_inputs.knowledge(args.dir, args.ids)
        elif args.action == "refresh":
            out = run_cmd.refresh(args.dir)
        elif args.action == "resume":
            errors = run_inputs.resume_errors(args.dir)
            out = {"result": "fail" if errors else "pass", "errors": errors}
        elif args.action == "judgment":
            out = judgment.check(args.dir, args.case)
        elif args.action == "compare":
            out = run_compare.compare(args.dir, args.other)
        else:
            final = args.event == "completed" or args.status in ("completed", "unresolved")
            if final:
                run_cmd.refresh(args.dir)
            before = (args.dir / "run.yaml").read_bytes()
            out = run_inputs.event(args.dir, args.event, args.note, args.status)
            # 終了後の状態で契約検査を行い、通らなければ実行記録を戻す
            checked = contracts.check_bundle(args.dir) if final else {"result": "pass"}
            if checked["result"] != "pass":
                (args.dir / "run.yaml").write_bytes(before)
                raise ValueError("契約検査に通らないため終了できない: " + " / ".join(checked["errors"][:5]))
    except (ValueError, OSError, yamlio.DataError) as exc:
        sys.stdout.write(yamlio.dumps({"result": "fail", "error": str(exc)}))
        return 1
    sys.stdout.write(yamlio.dumps(out))
    return 1 if out.get("result") == "fail" else 0
