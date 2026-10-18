"""2つの実行の比較（NFR-02、AC-08）。

判断ID・対象・前提・根拠・採否の差分と、使ったSkill・知識・環境の差分を出す。
出力の一致や判断の一致率を品質の代わりにしない。差分は人や確認エージェントが説明に使う材料である。
"""

import json
from pathlib import Path

from . import yamlio

def _ordered(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))


FIELDS = {f: lambda d, key=f: d[key] for f in (
    "stage", "problem", "selected_method", "rationale", "alternatives_not_applicable_reason",
    "validation_status", "confidence", "confidence_basis", "classification", "skill_version", "library_version",
)} | {
    "target_ids": lambda d: sorted(d["target_ids"]),
    "assumptions": lambda d: _ordered(d["assumptions"]),
    "sources": lambda d: _ordered(d["sources"]),
    "alternatives": lambda d: _ordered(d["alternatives"]),
    "input_observations": lambda d: _ordered(d["input_observations"]),
    "counter_evidence": lambda d: _ordered(d["counter_evidence"]),
    "validation_record_ids": lambda d: sorted(d["validation_record_ids"]),
}


def _current(directory: Path) -> dict[str, dict]:
    """判断IDごとの現行版。active がなければ最新の版（置換・撤回の状態を差分に出す）。"""
    latest: dict[str, dict] = {}
    for d in yamlio.load(directory / "decisions.yaml")["decisions"]:
        prev = latest.get(d["decision_id"])
        key = (d["status"] == "active", d["decision_revision"])
        if prev is None or key > (prev["status"] == "active", prev["decision_revision"]):
            latest[d["decision_id"]] = d
    return latest


def _named(items: list[dict]) -> dict[str, str]:
    return {i["name"]: i["sha256"] for i in items}


def _diff_map(a: dict, b: dict) -> dict:
    return {"added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()),
            "changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k])}


def compare(base: Path, other: Path) -> dict:
    runs = [yamlio.load(d / "run.yaml") for d in (base, other)]
    decisions = [_current(d) for d in (base, other)]
    changed = {}
    for did in sorted(decisions[0].keys() & decisions[1].keys()):
        a, b = decisions[0][did], decisions[1][did]
        fields = {f: {"base": get(a), "other": get(b)} for f, get in FIELDS.items() if get(a) != get(b)}
        if a["status"] != b["status"]:
            fields["status"] = {"base": a["status"], "other": b["status"]}
        if fields:
            changed[did] = {"stage": [a["stage"], b["stage"]], "fields": fields}
    env = [r["environment"] for r in runs]
    return {
        "result": "pass",
        "runs": [r["run_id"] for r in runs],
        "task_types": [r["task_type"] for r in runs],
        "environment": {"base": env[0], "other": env[1]} if env[0] != env[1] else "same",
        "skills": _diff_map(*(_named(r["skills"]) for r in runs)),
        "knowledge": _diff_map(*(_named(r["knowledge"]) for r in runs)),
        "decisions": {"only_in_base": sorted(decisions[0].keys() - decisions[1].keys()),
                      "only_in_other": sorted(decisions[1].keys() - decisions[0].keys()),
                      "changed": changed},
        "note": "判断の一致率や出力の一致は品質の指標にしない。差分の理由は採否・対象・根拠・前提・検証状況で説明する",
    }
