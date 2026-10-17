"""ルーブリックによる内容判定の記録の検査（AC-09、D-M4-09）。

判定の中身（設計が適切か）は判定エージェントが決める。ここでは、判定が独立したエージェントによること、
ケースの必須条件・ルーブリック全項目・人の確認方法を漏れなく判定していること、判定時点の現行版を対象にしたこと、
合否が個々の判定と矛盾しないことを確かめる。
"""

from pathlib import Path

from . import yamlio
from .cases import CASES

FILE = "rubric-judgment.yaml"


def _expected_verdict(j: dict, run: dict, findings: dict | None) -> str:
    # 残置を了承した（accepted）重大な指摘も、解消していないため合格を妨げる
    unresolved = [f for f in (findings or {}).get("findings", [])
                  if f["severity"] == "critical" and f["status"] != "resolved"]
    ok = (run["status"] == "completed" and not unresolved
          and all(c["result"] == "met" for c in j["conditions"] + j["human_checks"])
          and all(i["result"] != "fail" for i in j["items"])
          and not any(i["severity"] == "critical" for i in j["issues"]))
    return "pass" if ok else "fail"


def errors(directory: Path, case_dir: Path) -> list[str]:
    j = yamlio.load(directory / FILE)
    found = yamlio.schema_errors(j, "rubric-judgment.schema.json")
    if found:
        return [f"{FILE} {e}" for e in found]
    run = yamlio.load(directory / "run.yaml")
    case = yamlio.load(case_dir / "case.yaml")
    rubric = yamlio.load(case_dir.parent / "rubric.yaml")
    findings_path = directory / "findings.yaml"
    findings = yamlio.load(findings_path) if findings_path.is_file() else None
    out = []
    env = run["environment"]
    if (j["judge"]["host"], j["judge"]["model"]) == (env["host"], env["model"]):
        out.append(f"判定者 {j['judge']['host']}/{j['judge']['model']} が成果物を作ったエージェントと同じ（自己評価は判定に使わない）")
    if j["run_id"] != run["run_id"]:
        out.append(f"run_id {j['run_id']} が run.yaml の {run['run_id']} と異なる")
    if run["task_type"] != case["task_type"]:
        out.append(f"課題の種類 {run['task_type']} がケース {case['case_id']} の {case['task_type']} と異なる")
    for key in ("case_id", "case_version", "rubric_version"):
        if j[key] != case[key]:
            out.append(f"{key} {j[key]} がケースの {case[key]} と異なる")
    if j["rubric_version"] != rubric["rubric_version"]:
        out.append(f"rubric_version {j['rubric_version']} が固定ルーブリックの {rubric['rubric_version']} と異なる")
    current = {(a["artifact_id"], a["version"]) for a in run["artifacts"] if a["status"] == "current"}
    judged = {(a["artifact_id"], a["version"]) for a in j["artifacts"]}
    out += [f"現行の成果物 {a} v{v} を判定していない" for a, v in sorted(current - judged)]
    out += [f"判定した成果物 {a} v{v} は現行版ではない" for a, v in sorted(judged - current)]
    wanted = list(case["required_conditions"])
    if [c["text"] for c in j["conditions"]] != wanted:
        out.append("conditions がケースの required_conditions と同じ文・同じ順になっていない")
    scenarios = [s["scenario"] for s in case["scenarios"] if s["human_check"]]
    if [c["text"] for c in j["human_checks"]] != scenarios:
        out.append(f"human_checks がケースの人の確認方法を持つ境界条件 {scenarios} と対応しない")
    applies = {r["item"]: r["applies"] for r in case["rubric"]}
    items = [i["item"] for i in j["items"]]
    if sorted(items) != sorted(i["item"] for i in rubric["items"]):
        out.append(f"items がルーブリックの全項目を1件ずつ含まない（記載: {items}）")
    for i in j["items"]:
        if applies.get(i["item"]) is False and i["result"] != "not_applicable":
            out.append(f"{i['item']}: ケースで非該当の項目を {i['result']} と判定している")
        if applies.get(i["item"]) and i["result"] == "not_applicable":
            out.append(f"{i['item']}: ケースで適用する項目を非該当にしている")
        if i["result"] != "not_applicable" and not i["basis"]:
            out.append(f"{i['item']}: 判定の根拠（basis）がない")
    out += [f"必須条件「{c['text'][:20]}…」: 判定の根拠（basis）がない" for c in j["conditions"] if not c["basis"]]
    expected = _expected_verdict(j, run, findings)
    if j["verdict"] != expected:
        out.append(f"verdict {j['verdict']} が個々の判定・実行の状態・未解決の重大な指摘から導く {expected} と異なる")
    return out


def check(directory: Path, case: str) -> dict:
    case_dir = Path(case) if Path(case).is_dir() else CASES / case
    try:
        found = errors(directory, case_dir)
    except (OSError, ValueError, yamlio.DataError) as exc:
        found = [f"読込み失敗: {exc}"]
    return {"check": "rubric_judgment", "result": "fail" if found else "pass", "errors": found}
