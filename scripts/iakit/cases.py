"""課題ケースとルーブリックの固定状態の検査（10章、AC-06、AC-09）。

ケースの入力はハッシュで版を固定する。境界条件ごとに、原典のないCIで動く検査関数が実在することを確かめる。
内容の合否は人がルーブリックで判定し、この検査では判定しない。
"""

import re
from pathlib import Path

from . import manifest, yamlio
from .paths import ROOT

CASES = ROOT / "tests" / "cases"
MVP_TASK_TYPES = {"web_ia", "taxonomy"}
REQUIRED_SCENARIOS = {"missing_material", "homonym", "conflicting_evidence", "unsupported_route", "no_measurement",
                      "refuted", "mixed_results", "confidential", "injected_instruction", "stale", "refine_limit",
                      "resume"}


def _test_exists(ref: str) -> bool:
    path, _, name = ref.partition("::")
    file = ROOT / path
    return file.is_file() and re.search(rf"^def {re.escape(name)}\(", file.read_text(encoding="utf-8"), re.M) is not None


def _change_errors(tag: str, version: str, changes: list[dict]) -> list[str]:
    if version != "1.0.0" and version not in {c["version"] for c in changes}:
        return [f"{tag}: 版 {version} の変更理由が changes にない"]
    return []


def case_errors(case: dict, case_dir: Path, rubric: dict) -> list[str]:
    tag = case["case_id"]
    errors = _change_errors(tag, case["case_version"], case.get("changes", []))
    if case["rubric_version"] != rubric["rubric_version"]:
        errors.append(f"{tag}: rubric_version が固定ルーブリック {rubric['rubric_version']} と異なる")
    items = [i["item"] for i in rubric["items"]]
    covered = [r["item"] for r in case["rubric"]]
    if sorted(covered) != sorted(items):
        errors.append(f"{tag}: ルーブリック全項目に合格条件か非該当理由を1件ずつ記す（記載: {covered}）")
    for d in case["inappropriate_designs"]:
        errors += [f"{tag}: 不適切な案が未定義の項目 {v} を参照している" for v in d["violates"] if v not in items]
    for i in case["inputs"]:
        path = case_dir / i["path"]
        if not path.is_file():
            errors.append(f"{tag}: 入力 {i['path']} がない")
        elif manifest.sha256_file(path) != i["sha256"]:
            errors.append(f"{tag}: 入力 {i['path']} が固定した版と異なる（変更時は case_version と changes を更新する）")
        elif b"\r" in path.read_bytes():
            errors.append(f"{tag}: 入力 {i['path']} にCRが含まれる（LFに固定する）")
    task_ids = [t["task_id"] for t in case["tasks"]]
    if len(set(task_ids)) != len(task_ids):
        errors.append(f"{tag}: task_id が重複している")
    if case["kind"] == "representative" and not any(t["priority"] == "primary" for t in case["tasks"]):
        errors.append(f"{tag}: 代表ケースには主要タスクが必要")
    for s in case["scenarios"]:
        if not s["machine_checks"] and not s["human_check"]:
            errors.append(f"{tag}: 境界条件「{s['scenario']}」に検査も人の確認方法もない")
        errors += [f"{tag}: 検査 {ref} が見つからない" for ref in s["machine_checks"] if not _test_exists(ref)]
    return errors


def check(root: Path = CASES) -> dict:
    errors: list[str] = []
    try:
        rubric = yamlio.load(root / "rubric.yaml")
    except (OSError, ValueError) as exc:
        return {"check": "cases", "result": "fail", "errors": [f"rubric.yaml: 読込み失敗: {exc}"]}
    errors += [f"rubric.yaml {e}" for e in yamlio.schema_errors(rubric, "rubric.schema.json")]
    if not errors:
        errors += _change_errors("rubric.yaml", rubric["rubric_version"], rubric.get("changes", []))
    if errors:
        return {"check": "cases", "result": "fail", "errors": errors}
    cases = []
    for path in sorted(root.glob("*/case.yaml")):
        try:
            case = yamlio.load(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{path.parent.name}: 読込み失敗: {exc}")
            continue
        found = yamlio.schema_errors(case, "case.schema.json")
        errors += [f"{path.parent.name}/case.yaml {e}" for e in found]
        if not found:
            if case["case_id"].removeprefix("CASE-").lower() != path.parent.name:
                errors.append(f"{path.parent.name}: case_id とディレクトリ名が対応しない")
            errors += case_errors(case, path.parent, rubric)
            cases.append(case)
    present = {c["task_type"] for c in cases if c["kind"] == "representative"}
    if not MVP_TASK_TYPES <= present:
        errors.append(f"MVPの代表ケースが不足している: {sorted(MVP_TASK_TYPES - present)}")
    kinds = {s["kind"] for c in cases for s in c["scenarios"]}
    if not REQUIRED_SCENARIOS <= kinds:
        errors.append(f"境界条件が不足している: {sorted(REQUIRED_SCENARIOS - kinds)}")
    return {
        "check": "cases", "result": "fail" if errors else "pass",
        "cases": {c["case_id"]: c["case_version"] for c in cases},
        "rubric_version": rubric.get("rubric_version"), "content_review": "human_only", "errors": errors,
    }
