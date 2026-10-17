"""実行の成果物一式の検査: 経路ごとの必須成果物、Markdown成果物、評価の指摘、取込み結果（FR-06〜FR-08、IN-04）。"""

import re
from pathlib import Path

from . import yamlio
from .decisions import latest_active

STAGE_KINDS = {
    "analyze": ["problem_definition", "information_audit", "content_inventory"],
    "classify": ["taxonomy", "controlled_vocabulary", "classification_options"],
    "structure": ["information_architecture", "sitemap"],
    "validate": ["ia_review", "validation_plan", "review_findings"],
}
MARKDOWN_KINDS = {"problem_definition", "information_audit", "classification_options", "information_architecture",
                  "ia_review", "validation_plan"}
ASSUMPTION_HEADING = "## 仮定に基づく箇所"
DECISION_ID = re.compile(r"\bIA-[0-9]{3}\b(?! r[0-9])")
DECISION_REF = re.compile(r"\b(IA-[0-9]{3}) r([0-9]+)\b")


def required_errors(run: dict) -> list[str]:
    """完了・未解決で終えた対応経路は、各段階の成果物をすべて持つ。"""
    if not run["route"]["supported"] or run["status"] not in ("completed", "unresolved"):
        return []
    present = {a["kind"] for a in run["artifacts"]}
    return [f"run.yaml: 段階 {stage} の成果物 {kind} がない" for stage in run["route"]["stages"]
            for kind in STAGE_KINDS.get(stage, []) if kind not in present]


def markdown_errors(directory: Path, run: dict, decisions: list[dict]) -> list[str]:
    errors, known = [], {(d["decision_id"], d["decision_revision"]) for d in decisions}
    active = latest_active(decisions)
    for a in (a for a in run["artifacts"] if a["kind"] in MARKDOWN_KINDS):
        path = directory / a["path"]
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        tag = f"{a['artifact_id']}（{a['path']}）"
        first = next((ln.strip() for ln in text.splitlines() if ln.startswith("## ")), None)
        if first != ASSUMPTION_HEADING:
            errors.append(f"{tag}: 最初の節を「{ASSUMPTION_HEADING}」にする（仮定がなければ「なし」と書く）")
        errors += [f"{tag}: 判断 {m.group(0)} を版なしで参照している（{m.group(0)} rN と書く）"
                   for m in DECISION_ID.finditer(text)]
        for m in DECISION_REF.finditer(text):
            key = (m.group(1), int(m.group(2)))
            if key not in known:
                errors.append(f"{tag}: 判断 {m.group(0)} が判断記録にない")
            elif a["status"] == "current" and key not in active:
                errors.append(f"{tag}: 現行でない判断 {m.group(0)} を参照している")
    plan = next((a for a in run["artifacts"] if a["kind"] == "validation_plan"), None)
    if plan and (directory / plan["path"]).is_file():
        text = (directory / plan["path"]).read_text(encoding="utf-8")
        refs = {(m.group(1), int(m.group(2))) for m in DECISION_REF.finditer(text)}
        errors += [f"validation_plan: planned の判断 {d['decision_id']} r{d['decision_revision']} に検証方法が対応付いていない"
                   for d in decisions if (d["decision_id"], d["decision_revision"]) in active
                   and d["validation_status"] == "planned" and (d["decision_id"], d["decision_revision"]) not in refs]
    return errors


def findings_errors(doc: dict, run: dict) -> list[str]:
    errors, seen = [], set()
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    for f in doc["findings"]:
        fid = f["finding_id"]
        if fid in seen:
            errors.append(f"{fid}: finding_id が重複している")
        seen.add(fid)
        target = arts.get(f["target"]["artifact_id"])
        if target is None or f["target"]["version"] > target["version"]:
            errors.append(f"{fid}: 対象 {f['target']['artifact_id']} v{f['target']['version']} が実行記録にない")
        if f["round"] > run["refine"]["rounds"]:
            errors.append(f"{fid}: round {f['round']} が実行記録の巡数 {run['refine']['rounds']} を超える")
        r = f["resolved_in"]
        if r and (r["artifact_id"] != f["target"]["artifact_id"] or r["version"] <= f["target"]["version"]
                  or r["version"] > (target or {}).get("version", 0)):
            errors.append(f"{fid}: resolved_in は対象と同じ成果物の、指摘より新しい登録済みの版にする")
        if f["status"] == "unresolved" and run["refine"]["rounds"] < 2 and f["severity"] == "critical":
            errors.append(f"{fid}: 2巡に達する前の重大な指摘は open のまま修正する")
    open_critical = sorted(f["finding_id"] for f in doc["findings"]
                           if f["severity"] == "critical" and f["status"] in ("open", "unresolved"))
    if open_critical != sorted(run["refine"]["unresolved_critical"]):
        errors.append(f"refine.unresolved_critical {run['refine']['unresolved_critical']} が指摘の状態 {open_critical} と一致しない")
    return errors


def ingest_errors(directory: Path, inputs_doc: dict) -> list[str]:
    """取込み結果があれば、入力のハッシュと一致し、スキーマに合うこと。"""
    errors = []
    for i in inputs_doc["inputs"]:
        path = directory / "ingest" / f"{i['input_id']}.yaml"
        if not path.is_file():
            continue
        doc = yamlio.load(path)
        found = yamlio.schema_errors(doc, "ingest.schema.json")
        if found:
            errors += [f"ingest/{path.name} {e}" for e in found[:3]]
        elif doc["sha256"] != i["sha256"] or doc["input_id"] != i["input_id"]:
            errors.append(f"ingest/{path.name}: 入力 {i['input_id']} の現在の実体と対応しない（再取込みする）")
        elif doc["unreadable"] != i["unreadable"]:
            errors.append(f"{i['input_id']}: unreadable が取込み結果と一致しない")
    return errors


def check(directory: Path, docs: dict) -> list[str]:
    run = docs.get("run.yaml")
    found = ingest_errors(directory, docs["inputs.yaml"]) if "inputs.yaml" in docs else []
    if not run:
        return found
    decisions = docs.get("decisions.yaml", {"decisions": []})["decisions"]
    found += required_errors(run) + markdown_errors(directory, run, decisions)
    reg = next((a for a in run["artifacts"] if a["kind"] == "review_findings"), None)
    doc = docs.get("findings.yaml")
    if doc:
        found += findings_errors(doc, run)
        if reg is None or (reg["artifact_id"], reg["version"], reg["classification"]) != (
                doc["artifact_id"], doc["version"], doc["classification"]):
            found.append("findings.yaml: artifact_id・版・機密区分が実行記録の review_findings と一致しない")
    elif reg:
        found.append("findings.yaml: 登録された成果物のデータがない")
    if reg and reg["path"] != "findings.yaml":
        found.append("review_findings: 指摘は実行ディレクトリ直下の findings.yaml に置く")
    return found
