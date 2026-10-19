"""記録作業のコマンド化（roadmap-autonomy P1）: 版の書き換え、判断・指摘・回答の登録、ツールの不具合の回帰。"""

import json
import shutil

import pytest

from iakit import cli, contracts, decision_cmd, finding_cmd, question_cmd, run_cmd, run_inputs, run_outputs, yamlio
from iakit.paths import ROOT

CASE = ROOT / "tests/cases/webia-001/inputs"
FIXTURE = ROOT / "tests/fixtures/contracts/webia-run"


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "RUN-T"
    run_cmd.init(d, "RUN-T", "web_ia", "claude-code", "test-model")
    run_inputs.add(d, CASE / "brief.md", "brief", "依頼の説明")
    return d


@pytest.fixture
def bundle(tmp_path):
    d = tmp_path / "run"
    shutil.copytree(FIXTURE, d)
    return d


def audit(d, header="", body="## 仮定に基づく箇所\n\nなし\n"):
    text = f"# 情報監査\n\n{header}- 機密区分: internal\n\n" + body
    text += "".join(f"\n## {h}\n\n検査用。\n" for h in run_outputs.MARKDOWN_HEADINGS["information_audit"])
    (d / "information-audit.md").write_text(text, encoding="utf-8")


def errors(d):
    return contracts.check_bundle(d)["errors"]


# --- P1-4 ツールの不具合 ----------------------------------------------------

def test_run_id_containing_ia_number_is_not_a_decision_reference(tmp_path):
    d = tmp_path / "RUN-OHP-IA-001"
    run_cmd.init(d, "RUN-OHP-IA-001", "web_ia", "claude-code", "test-model")
    run_inputs.add(d, CASE / "brief.md", "brief", "依頼の説明")
    audit(d, header="- 実行: RUN-OHP-IA-001\n")
    run_cmd.artifact(d, "information-audit.md", "information_audit", ["INPUT-001"], [])
    assert errors(d) == []
    audit(d, body="## 仮定に基づく箇所\n\n判断IA-002の仮定\n")
    with pytest.raises(ValueError, match="IA-002 を版なしで参照"):
        run_cmd.artifact(d, "information-audit.md", "information_audit", ["INPUT-001"], [])


def test_absolute_or_cwd_relative_path_is_recorded_relative(run_dir, monkeypatch):
    audit(run_dir)
    run_cmd.artifact(run_dir, str((run_dir / "information-audit.md").absolute()), "information_audit", ["INPUT-001"], [])
    assert yamlio.load(run_dir / "run.yaml")["artifacts"][0]["path"] == "information-audit.md"
    monkeypatch.chdir(run_dir.parent)
    assert run_cmd.artifact(run_dir, "RUN-T/information-audit.md", "information_audit", [], [])["version"] == 1
    outside = run_dir.parent / "outside.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="実行ディレクトリ .* の外"):
        run_cmd.artifact(run_dir, str(outside), "problem_definition", [], [])


# --- P1-1 版の自動更新 ------------------------------------------------------

def test_dependency_versions_are_rewritten_and_unchanged_stale_needs_no_impact(bundle):
    inv = bundle / "content-inventory.yaml"
    inv.write_text(inv.read_text(encoding="utf-8").replace("version: 1", "version: 1\n# 改訂"), encoding="utf-8")
    staled = run_cmd.artifact(bundle, "content-inventory.yaml", "content_inventory", [], [])["staled"]
    assert "ART-003" in staled and "ART-004" in staled
    assert yamlio.load(inv)["version"] == 2
    with pytest.raises(ValueError, match="--no-impact"):
        run_cmd.artifact(bundle, "taxonomy.yaml", "taxonomy", [], [])
    out = run_cmd.artifact(bundle, "taxonomy.yaml", "taxonomy", [], [], no_impact="項目の追加はない")
    taxonomy = yamlio.load(bundle / "taxonomy.yaml")
    assert (taxonomy["version"], taxonomy["inventory"]) == (2, {"artifact_id": "ART-001", "version": 2})
    assert (bundle / "taxonomy.yaml").read_text(encoding="utf-8").startswith("# 契約検査用の自作例")
    decisions = yamlio.load(bundle / "decisions.yaml")["decisions"]
    record = next(d for d in decisions if f"{d['decision_id']} r1" == out["no_impact_decision"])
    assert record["target_ids"] == ["ART-003"] and record["rationale"] == "項目の追加はない"
    with pytest.raises(ValueError, match="stale の成果物を再登録するときだけ"):
        run_cmd.artifact(bundle, "taxonomy.yaml", "taxonomy", [], [], no_impact="再度")


def test_markdown_header_versions_are_rewritten(run_dir):
    audit(run_dir)
    run_cmd.artifact(run_dir, "information-audit.md", "information_audit", ["INPUT-001"], [])
    (run_dir / "problem-definition.md").write_text(
        "# 問題定義\n\n- 対象: 情報監査 ART-001 v9\n- 機密区分: public\n\n## 仮定に基づく箇所\n\nART-001 v9 は本文\n"
        + "".join(f"\n## {h}\n\n検査用。\n" for h in run_outputs.MARKDOWN_HEADINGS["problem_definition"]),
        encoding="utf-8")
    run_cmd.artifact(run_dir, "problem-definition.md", "problem_definition", ["ART-001"], [])
    text = (run_dir / "problem-definition.md").read_text(encoding="utf-8")
    assert "情報監査 ART-001 v1" in text and "機密区分: internal" in text and "ART-001 v9 は本文" in text


def test_registration_rejects_wrong_reference_kind_and_contract_errors(bundle, run_dir):
    path = bundle / "taxonomy.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("vocabulary: {artifact_id: ART-002", "vocabulary: {artifact_id: ART-001"), encoding="utf-8")
    with pytest.raises(ValueError, match="vocabulary の参照 ART-001 は controlled_vocabulary ではない（登録済みの controlled_vocabulary は ART-002）"):
        run_cmd.artifact(bundle, "taxonomy.yaml", "taxonomy", [], [])
    # 契約に合わない成果物は登録せず、書き換えた本文も戻す
    audit(run_dir, header="- 対象: ART-009 v9\n", body="## 概要\n\nIA-001 を参照\n")
    before = (run_dir / "information-audit.md").read_bytes()
    with pytest.raises(ValueError, match="登録しない（契約に合わない）"):
        run_cmd.artifact(run_dir, "information-audit.md", "information_audit", ["INPUT-001"], [])
    assert (run_dir / "information-audit.md").read_bytes() == before
    assert yamlio.load(run_dir / "run.yaml")["artifacts"] == []


def test_superseded_decision_references_are_rewritten_to_current(bundle):
    decision_cmd.revise(bundle, "IA-002", {"rationale": "見直した", "validation_status": "not_tested",
                                           "validation_record_ids": []})
    path = bundle / "information-architecture.md"
    assert "IA-002 r1" in path.read_text(encoding="utf-8")
    run_cmd.artifact(bundle, "information-architecture.md", "information_architecture", ["ART-004"], [],
                     no_impact="理由の言い換えだけで構造は変わらない")
    assert "IA-002 r2" in path.read_text(encoding="utf-8") and "IA-002 r1" not in path.read_text(encoding="utf-8")
    decision_cmd.revise(bundle, "IA-001", {"rationale": "見直した"})
    run_cmd.artifact(bundle, "taxonomy.yaml", "taxonomy", [], [], no_impact="分類は変わらない")
    assert {s["decision_ref"]["decision_revision"] for s in yamlio.load(bundle / "taxonomy.yaml")["schemes"]} == {3}


# --- P1-2 判断・指摘の追加 --------------------------------------------------

DECISION = {"stage": "structure", "target_ids": ["NODE-010"], "problem": "料金と申込みを探しにくい",
            "selected_method": "料金・申込みを独立したノードにする", "rationale": "依頼の説明に申込みの声がある",
            "input_observations": [{"observation_id": "OBS-010", "input_id": "INPUT-001", "locator": "brief.md#L6",
                                    "observation": "申込み方法と料金を探すのに時間がかかる"}],
            "alternatives": [{"method": "各サービスページに置く", "rejected_because": "重複する"}],
            "confidence_basis": "入力の観察だけに基づく"}


def test_decision_add_fills_derived_values_and_rejects_bad_records(bundle):
    out = decision_cmd.add(bundle, DECISION)
    assert out["decision"] == "IA-004 r1" and out["confidence"] == "medium" and out["classification"] == "internal"
    assert errors(bundle) == []
    before = (bundle / "decisions.yaml").read_bytes()
    bad = dict(DECISION, alternatives=[{"method": "x", "reason": "項目名の誤り"}])
    with pytest.raises(ValueError, match="スキーマに合わない"):
        decision_cmd.add(bundle, bad)
    with pytest.raises(ValueError, match="契約検査に通らない"):
        decision_cmd.add(bundle, dict(DECISION, target_ids=["NODE-999"]))
    with pytest.raises(ValueError, match="コマンドが決める"):
        decision_cmd.add(bundle, dict(DECISION, decision_id="IA-009"))
    assert (bundle / "decisions.yaml").read_bytes() == before


def test_decision_revise_supersedes_and_stales_dependents(bundle):
    out = decision_cmd.revise(bundle, "IA-002", {"rationale": "回答で利用者の起点が確かめられた",
                                                 "validation_status": "not_tested", "validation_record_ids": []})
    assert out["decision"] == "IA-002 r2" and out["superseded"] == "IA-002 r1" and out["confidence"] == "medium"
    assert "ART-008" in out["staled"]
    revs = [d for d in yamlio.load(bundle / "decisions.yaml")["decisions"] if d["decision_id"] == "IA-002"]
    assert [d["status"] for d in revs] == ["superseded", "active"]
    with pytest.raises(ValueError, match="superseded で、改訂できない|は判断記録にない"):
        decision_cmd.revise(bundle, "IA-099", {})
    assert decision_cmd.withdraw(bundle, "IA-003")["withdrawn"] == "IA-003 r1"


def test_finding_add_and_resolve(bundle):
    out = finding_cmd.add(bundle, {"severity": "major", "target": {"artifact_id": "ART-004"}, "locator": "NODE-011",
                                   "problem": "問い合わせの導線が弱い", "basis": ["R-03"], "recommendation": "ナビに出す",
                                   "resolution_condition": "主要ナビに掲載される", "return_to": "structure"})
    assert out["finding_id"] == "FIND-002"
    found = yamlio.load(bundle / "findings.yaml")["findings"][-1]
    rounds = yamlio.load(bundle / "run.yaml")["refine"]["rounds"]
    assert found["target"] == {"artifact_id": "ART-004", "version": 1} and found["round"] == rounds
    with pytest.raises(ValueError, match="ART-099 が実行記録にない"):
        finding_cmd.add(bundle, {"severity": "major", "target": {"artifact_id": "ART-099"}})
    with pytest.raises(ValueError, match="書き込まない"):
        finding_cmd.add(bundle, {"severity": "major", "target": {"artifact_id": "ART-004", "version": 1}})
    with pytest.raises(ValueError, match="より新しい登録済みの版"):
        finding_cmd.resolve(bundle, "FIND-002", "resolved")
    assert finding_cmd.resolve(bundle, "FIND-002", "accepted", note="ユーザーが残置を決めた")["status"] == "accepted"
    run_cmd.artifact(bundle, "findings.yaml", "review_findings", [], [])
    assert errors(bundle) == []


def test_cli_reads_json_argument(bundle, capsys):
    assert cli.main(["decision", "add", str(bundle), "--json", json.dumps(DECISION, ensure_ascii=False)]) == 0
    assert "IA-004 r1" in capsys.readouterr().out
    assert cli.main(["decision", "add", str(bundle)]) == 1


# --- P1-3 回答の登録 --------------------------------------------------------

def test_question_answer_registers_input_and_lists_affected(bundle, tmp_path):
    (bundle / "questions").mkdir()
    (bundle / "questions/Q-001.md").write_text("# Q-001\n\n1. 入口は2つでよいか（IA-002 r1）\n", encoding="utf-8")
    answer = tmp_path / "answer.md"
    answer.write_text("入口は2つでよい。\n", encoding="utf-8")
    out = question_cmd.answer(bundle, "Q-001", answer)
    assert out["decisions"] == ["IA-002 r1"] and "ART-008" in out["artifacts"]
    registered = yamlio.load(bundle / "inputs.yaml")["inputs"][-1]
    assert (registered["input_id"], registered["kind"]) == (out["input_id"], "hearing_answer")
    with pytest.raises(ValueError, match="Q-002.md がない"):
        question_cmd.answer(bundle, "Q-002", answer)
