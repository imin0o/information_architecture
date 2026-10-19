"""評価の指摘の追加と状態の更新（P1-2、FR-06）。

findings.yaml だけを書き換え、スキーマと指摘ごとの整合性に合わなければ書き込まない。
成果物としての登録（版・機密区分・未解決の重大な指摘の同期）は iakit run artifact で行う。
"""

from pathlib import Path

from . import yamlio
from .run_cmd import load
from .run_outputs import finding_item_errors

NAME = "findings.yaml"
AUTO = ("finding_id", "round", "status", "resolved_in")
NEXT = "指摘をすべて書いたら iakit run artifact <dir> findings.yaml --kind review_findings で登録する"


def _doc(directory: Path, run: dict, rubric_version: str | None) -> dict:
    if (directory / NAME).is_file():
        doc = yamlio.load(directory / NAME)
        if rubric_version:
            doc["rubric_version"] = rubric_version
        return doc
    reg = next((a for a in run["artifacts"] if a["kind"] == "review_findings"), None)
    return {"schema_version": "1.0", "run_id": run["run_id"],
            "artifact_id": reg["artifact_id"] if reg else f"ART-{len(run['artifacts']) + 1:03d}",
            "version": reg["version"] if reg else 1, "classification": "internal",
            "rubric_version": rubric_version, "findings": []}


def _write(directory: Path, doc: dict, run: dict) -> None:
    errors = yamlio.schema_errors(doc, "findings.schema.json") or finding_item_errors(doc, run)
    if errors:
        raise ValueError(f"{NAME} に書き込まない: " + " / ".join(errors[:5]))
    (directory / NAME).write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")


def _current(run: dict, artifact_id: str) -> int:
    found = next((a for a in run["artifacts"] if a["artifact_id"] == artifact_id), None)
    if found is None:
        raise ValueError(f"対象 {artifact_id} が実行記録にない")
    return found["version"]


def add(directory: Path, data: dict, rubric_version: str | None = None) -> dict:
    """target の version を省けば対象の現行版、round は実行記録の巡数にする。"""
    run = load(directory, "run.yaml")
    fixed = sorted(set(AUTO) & set(data))
    if fixed:
        raise ValueError(f"{fixed} はコマンドが決める。状態の変更は iakit finding resolve を使う")
    doc = _doc(directory, run, rubric_version)
    target = dict(data.get("target") or {})
    if "artifact_id" in target and "version" not in target:
        target["version"] = _current(run, target["artifact_id"])
    numbers = [int(f["finding_id"].split("-")[1]) for f in doc["findings"]]
    finding = {"finding_id": f"FIND-{max(numbers, default=0) + 1:03d}", "round": run["refine"]["rounds"],
               "note": None, **data, "target": target, "status": "open", "resolved_in": None}
    doc["findings"].append(finding)
    _write(directory, doc, run)
    return {"finding_id": finding["finding_id"], "round": finding["round"], "next": NEXT}


def resolve(directory: Path, finding_id: str, status: str, version: int | None = None,
            note: str | None = None) -> dict:
    """resolved は解消を確かめた対象の版（省けば現行版）を resolved_in に記録する。"""
    run = load(directory, "run.yaml")
    if not (directory / NAME).is_file():
        raise ValueError(f"{NAME} がない")
    doc = yamlio.load(directory / NAME)
    finding = next((f for f in doc["findings"] if f["finding_id"] == finding_id), None)
    if finding is None:
        raise ValueError(f"{finding_id} は {NAME} にない")
    aid = finding["target"]["artifact_id"]
    finding["status"] = status
    finding["resolved_in"] = ({"artifact_id": aid, "version": version or _current(run, aid)}
                              if status == "resolved" else None)
    if note is not None:
        finding["note"] = note
    _write(directory, doc, run)
    return {"finding_id": finding_id, "status": status, "resolved_in": finding["resolved_in"], "next": NEXT}
