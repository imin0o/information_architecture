"""独立した判定エージェントによるルーブリック判定の記録（AC-09、D-M4-09）。"""

import shutil

import pytest

from iakit import judgment, yamlio
from iakit.cases import CASES
from iakit.paths import ROOT

BUNDLE = ROOT / "tests" / "fixtures" / "contracts" / "webia-run"
CASE = CASES / "webia-001"


def record(run: dict) -> dict:
    case = yamlio.load(CASE / "case.yaml")
    check = lambda text: {"text": text, "result": "met", "basis": ["ART-006 v1"], "note": ""}  # noqa: E731
    return {
        "schema_version": "1.0", "run_id": run["run_id"], "case_id": case["case_id"],
        "case_version": case["case_version"], "rubric_version": case["rubric_version"],
        "judged_at": "2026-10-17T00:00:00+09:00",
        "judge": {"agent": "Codex", "host": "codex", "model": "gpt-test"},
        "artifacts": [{"artifact_id": a["artifact_id"], "version": a["version"]}
                      for a in run["artifacts"] if a["status"] == "current"],
        "conditions": [check(c) for c in case["required_conditions"]],
        "items": [{"item": r["item"], "result": "pass" if r["applies"] else "not_applicable",
                   "basis": ["ART-006 v1"] if r["applies"] else [], "note": ""} for r in case["rubric"]],
        "human_checks": [check(s["scenario"]) for s in case["scenarios"] if s["human_check"]],
        "issues": [], "verdict": "pass", "reason": "全条件を満たす",
    }


@pytest.fixture
def judge(tmp_path):
    def run(change=None):
        d = tmp_path / "run"
        shutil.copytree(BUNDLE, d)
        data = record(yamlio.load(d / "run.yaml"))
        if change:
            change(data)
        (d / judgment.FILE).write_text(yamlio.dumps(data), encoding="utf-8")
        return judgment.check(d, "webia-001")["errors"]
    return run


def has(errors, text):
    return any(text in e for e in errors)


def test_independent_judgment_passes(judge):
    assert judge() == []


def test_self_judgment_is_rejected(judge):
    assert has(judge(lambda j: j["judge"].update(host="claude-code", model="fixture")), "自己評価は判定に使わない")


def test_missing_condition_is_rejected(judge):
    assert has(judge(lambda j: j["conditions"].pop()), "required_conditions と同じ文・同じ順")


def test_missing_rubric_item_is_rejected(judge):
    assert has(judge(lambda j: j["items"].pop()), "ルーブリックの全項目を1件ずつ含まない")


def test_old_artifact_version_is_rejected(judge):
    def old(j):
        j["artifacts"][0]["version"] += 1
    errors = judge(old)
    assert has(errors, "を判定していない") and has(errors, "は現行版ではない")


def test_pass_with_failed_item_is_rejected(judge):
    assert has(judge(lambda j: j["items"][0].update(result="fail")), "verdict pass が")


def test_pass_with_critical_issue_is_rejected(judge):
    issue = {"severity": "critical", "item": "R-05", "problem": "主要タスクに到達できない", "basis": ["ART-006 v1"],
             "residual": "残せない"}
    assert has(judge(lambda j: j["issues"].append(issue)), "verdict pass が")


def test_fail_verdict_with_failed_item_passes_check(judge):
    def failed(j):
        j["items"][0]["result"] = "fail"
        j["verdict"] = "fail"
    assert judge(failed) == []


def test_accepted_critical_finding_blocks_pass(judge, tmp_path):
    def accepted(j):
        path = tmp_path / "run" / "findings.yaml"
        doc = yamlio.load(path)
        doc["findings"][0].update(severity="critical", status="accepted")
        path.write_text(yamlio.dumps(doc), encoding="utf-8")
    assert has(judge(accepted), "verdict pass が")


def test_issue_requires_residual_reason(judge):
    issue = {"severity": "minor", "item": "R-03", "problem": "表記の揺れ", "basis": ["ART-006 v1"]}
    assert has(judge(lambda j: j["issues"].append(issue)), "residual")


def test_item_without_basis_is_rejected(judge):
    assert has(judge(lambda j: j["items"][0].update(basis=[])), "判定の根拠（basis）がない")
