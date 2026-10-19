"""一括実行の補助（P3）: 常設の許可、段階の契約検査、独立判定の自動実行、終了報告。"""

import shutil

import pytest
from test_judgment import record

from iakit import cli, contracts, gate, judge_cmd, report, run_cmd, run_inputs, standing, yamlio
from iakit.paths import ROOT

BUNDLE = ROOT / "tests" / "fixtures" / "contracts" / "webia-run"


def profile_entry(entry, destination, kind, level="internal", kinds=("input", "derived")):
    return {"entry": entry, "destination": destination, "destination_kind": kind, "max_classification": level,
            "data_kinds": list(kinds), "purpose": "検査用の常設の許可", "granted_at": "2026-10-20T09:00:00+09:00"}


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    d = tmp_path / "run"
    shutil.copytree(BUNDLE, d)
    monkeypatch.setenv("IAKIT_PROFILE", str(tmp_path / "profile.yaml"))
    return d


def write_profile(tmp_path, *entries):
    (tmp_path / "profile.yaml").write_text(yamlio.dumps({"schema_version": "1.0", "approvals": list(entries)}),
                                           encoding="utf-8")


def test_standing_approvals_are_copied_within_scope(run_dir, tmp_path):
    write_profile(tmp_path, profile_entry("host", "claude-code", "host_llm"),
                  profile_entry("judge", "codex", "external_service"),
                  profile_entry("self-judge", "claude-code", "external_service"),
                  profile_entry("public-only", "claude-code", "host_llm", level="public"))
    out = standing.sync(run_dir)
    assert [a["entry"] for a in out["applied"]] == ["host", "judge"]
    skipped = {s["entry"]: s["reasons"] for s in out["skipped"]}
    assert "独立判定の相手" in skipped["self-judge"][0] and "入力がない" in skipped["public-only"][0]
    approvals = yamlio.load(run_dir / "run.yaml")["approvals"]
    assert [a["approval_id"] for a in approvals] == ["APPROVAL-001", "APPROVAL-002", "APPROVAL-003"]
    registered = [i["input_id"] for i in yamlio.load(run_dir / "inputs.yaml")["inputs"]]
    assert approvals[2]["input_ids"] == registered and approvals[2]["standing"]["entry"] == "judge"
    assert contracts.check_bundle(run_dir)["result"] == "pass"
    # 再実行しても同じIDを使い、増やさない
    assert standing.sync(run_dir)["applied"][1]["approval_id"] == "APPROVAL-003"
    assert len(yamlio.load(run_dir / "run.yaml")["approvals"]) == 3


def test_profile_outside_scope_is_rejected(run_dir, tmp_path):
    write_profile(tmp_path, profile_entry("wide", "codex", "external_service", level="confidential"))
    with pytest.raises(ValueError, match="max_classification"):
        standing.sync(run_dir)
    write_profile(tmp_path, profile_entry("judge", "codex", "external_service"))
    standing.sync(run_dir)
    run = yamlio.load(run_dir / "run.yaml")
    run["approvals"][-1]["max_classification"] = "confidential"
    (run_dir / "run.yaml").write_text(yamlio.dumps(run), encoding="utf-8")
    errors = contracts.check_bundle(run_dir)["errors"]
    assert any("常設 judge" in e and "常設にできない" in e for e in errors)


def test_no_profile_changes_nothing(run_dir):
    before = (run_dir / "run.yaml").read_bytes()
    assert standing.sync(run_dir)["profile"] is None
    assert (run_dir / "run.yaml").read_bytes() == before


def test_gate_interrupts_after_same_failure_three_times(run_dir):
    run_inputs.event(run_dir, "rerun", "検査")
    assert gate.gate(run_dir, "structure")["result"] == "pass"
    path = run_dir / "sitemap.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# 変更\n", encoding="utf-8")
    assert [gate.gate(run_dir, "structure")["failures"] for _ in range(2)] == [1, 2]
    assert gate.gate(run_dir, "classify")["failures"] == 1
    gate.gate(run_dir, "classify")
    out = gate.gate(run_dir, "classify")
    assert out["result"] == "interrupted"
    run = yamlio.load(run_dir / "run.yaml")
    assert run["status"] == "interrupted" and run["history"][-1]["event"] == "interrupted"
    assert cli.main(["run", "gate", str(run_dir), "--stage", "classify"]) == 1


def test_judge_not_run_without_approval(run_dir):
    calls = []
    out = judge_cmd.judge(run_dir, "webia-001", call=lambda *a: calls.append(a))
    assert out["result"] == "not_run" and "許可" in out["reason"] and calls == []
    assert yamlio.load(run_dir / "run.yaml")["history"][-1]["event"] == "judged"


def fake_judge(run_dir, change=None):
    def call(host, cwd, prompt, timeout):
        assert host == "codex" and "rubric-judgment.yaml" in prompt and "<run_id>" not in prompt
        data = record(yamlio.load(run_dir / "run.yaml"))
        if change:
            change(data)
        (run_dir / "rubric-judgment.yaml").write_text(yamlio.dumps(data), encoding="utf-8")
        return 0, "判定した"
    return call


def test_judge_records_transmission_and_passes(run_dir, tmp_path):
    write_profile(tmp_path, profile_entry("judge", "codex", "external_service"))
    out = judge_cmd.judge(run_dir, "webia-001", call=fake_judge(run_dir))
    assert (out["result"], out["verdict"], out["findings_added"]) == ("pass", "pass", [])
    run = yamlio.load(run_dir / "run.yaml")
    sent = [t for t in run["transmissions"] if t["destination"] == "codex"]
    assert [t["data_kind"] for t in sent] == ["input", "derived"] and sent[0]["approval_id"] == "APPROVAL-002"
    assert "codex/gpt-test verdict=pass" in run["history"][-1]["note"]
    assert contracts.check_bundle(run_dir)["result"] == "pass"


def test_judge_fail_returns_critical_issue_to_refine(run_dir, tmp_path):
    write_profile(tmp_path, profile_entry("judge", "codex", "external_service"))

    def change(data):
        data["items"][0].update(result="fail", note="主要タスクの経路がない")
        data["issues"] = [{"severity": "critical", "item": data["items"][0]["item"], "problem": "主要タスクに着けない",
                           "basis": ["ART-006 v1 節=主要タスク"], "residual": "残せない"}]
        data["verdict"] = "fail"
    out = judge_cmd.judge(run_dir, "webia-001", call=fake_judge(run_dir, change))
    assert out["verdict"] == "fail" and len(out["findings_added"]) == 1
    run = yamlio.load(run_dir / "run.yaml")
    assert run["status"] == "in_progress" and run["refine"]["unresolved_critical"] == out["findings_added"]
    found = yamlio.load(run_dir / "findings.yaml")["findings"][-1]
    assert (found["target"]["artifact_id"], found["return_to"], found["severity"]) == ("ART-006", "analyze", "critical")
    assert contracts.check_bundle(run_dir)["result"] == "pass"
    # 2回目の判定では旧い判定を残す
    write_profile(tmp_path, profile_entry("judge", "codex", "external_service"))
    run_inputs.event(run_dir, "completed", "", status="unresolved")
    judge_cmd.judge(run_dir, "webia-001", call=fake_judge(run_dir, change))
    assert (run_dir / "rubric-judgment.round1.yaml").is_file()


def test_report_separates_simulation_from_measurement(run_dir):
    out = report.write(run_dir)
    text = (run_dir / report.NAME).read_text(encoding="utf-8")
    for heading in ("## 成果物と版", "## 仮定", "## 検証の区別", "## 残る指摘", "## 独立判定", "## 人の判断が必要な事項"):
        assert heading in text
    assert "- 機密区分: internal" in text and "模擬評価と構造の測定は利用者の行動ではなく" in text
    assert "送信を止めた: web-search" in text and "独立判定が未実施" in text
    assert out["lines"] > 10
    assert cli.main(["run", "report", str(run_dir)]) == 0


def test_review_registration_warns_about_old_target_version(run_dir):
    run_inputs.event(run_dir, "rerun", "検査")
    path = run_dir / "ia-review.md"
    text = path.read_text(encoding="utf-8").replace("- 機密区分: internal", "- 機密区分: internal\n- 評価対象: ART-001 v9")
    path.write_text(text, encoding="utf-8")
    out = run_cmd.artifact(run_dir, "ia-review.md", "ia_review", [], [])
    assert "ART-001 v9（現行 v1）" in out["warnings"][0]
