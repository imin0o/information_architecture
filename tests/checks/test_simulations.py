"""模擬評価の記録（P2-2、P2-3）と、simulated・tested_* の区別（AC-07）。"""

import copy
import shutil

import pytest

from iakit import contracts, provenance, simulation_cmd, yamlio
from iakit.simulations import derive_results, record_errors

from conftest import BUNDLE


def has(errors, text):
    return any(text in e for e in errors)


def tree_test(answers=("NODE-009", "NODE-009", None)):
    """TASK-01（南地区）を3回試行した記録。"""
    return {"method": "tree_test", "persona": {"statement": "南地区に住む高齢の親を持つ家族"},
            "evaluator": {"host": "claude-code", "model": "m"}, "repetitions": len(answers),
            "items": [{"item_id": "TASK-01", "prompt": "南地区で使えるサービスを探す"}],
            "trials": [{"repetition": n, "item_id": "TASK-01", "first_choice": "NODE-006" if a else "NODE-002",
                        "answer": a} for n, a in enumerate(answers, start=1)]}


@pytest.fixture
def run_dir(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "run")
    return tmp_path / "run"


def test_kit_gives_labels_only(run_dir):
    kit = simulation_cmd.kit(run_dir, "tree_test")
    assert kit["tree"][0] == "- トップ [NODE-001]" and "  - 地区から探す [NODE-006]" in kit["tree"]
    assert kit["items"] == ["TASK-01", "TASK-02", "TASK-03", "TASK-04"]
    # 概念・判断・リンクなど設計の文脈は渡さない
    assert "CONCEPT" not in str(kit) and "IA-" not in str(kit)
    sort = simulation_cmd.kit(run_dir, "closed_card_sort")
    assert "サービスから探す [NODE-002]" in sort["categories"]


def test_add_derives_results_from_trials(run_dir):
    out = simulation_cmd.add(run_dir, tree_test())
    assert out["simulation_id"] == "SIM-001"
    assert out["results"] == [{"item_id": "TASK-01", "attempts": 3, "success_rate": 0.667, "direct_first_rate": 0.667,
                               "first_choices": {"NODE-002": 1, "NODE-006": 2}, "below_threshold": True}]
    sim = yamlio.load(run_dir / "simulations.yaml")["simulations"][0]
    assert sim["items"][0]["expected"] == ["NODE-009"] and sim["persona"]["source"] == {"artifact_id": "ART-005", "version": 1}
    assert contracts.check_bundle(run_dir)["errors"] == []


def test_add_rejects_self_reported_and_incomplete_records(run_dir):
    with pytest.raises(ValueError, match="コマンドが決める"):
        simulation_cmd.add(run_dir, dict(tree_test(), results=[]))
    with pytest.raises(ValueError, match="repetitions"):
        simulation_cmd.add(run_dir, dict(tree_test(), repetitions=5))
    with pytest.raises(ValueError, match="サイトマップにない"):
        simulation_cmd.add(run_dir, tree_test(answers=("NODE-099",)))
    assert not (run_dir / "simulations.yaml").exists()


def test_card_sort_expects_category_containing_the_card(run_dir):
    data = {"method": "closed_card_sort", "persona": {"statement": "利用を考える家族"},
            "evaluator": {"host": "codex", "model": "m"}, "repetitions": 2, "categories": ["NODE-002", "NODE-006"],
            "items": [{"item_id": "CONTENT-004", "prompt": "見守りサービスの案内"}],
            "trials": [{"repetition": n, "item_id": "CONTENT-004", "first_choice": c, "answer": c}
                       for n, c in ((1, "NODE-002"), (2, "NODE-006"))]}
    out = simulation_cmd.add(run_dir, data)
    # CONTENT-004 は見守り（サービス）と北地区（地区）の両方に置かれている
    assert out["results"][0]["success_rate"] == 1.0


def test_tampered_results_are_rejected(run_dir):
    simulation_cmd.add(run_dir, tree_test())
    doc = yamlio.load(run_dir / "simulations.yaml")
    doc["simulations"][0]["results"][0]["success_rate"] = 1.0
    arts = {a["artifact_id"]: a for a in yamlio.load(run_dir / "run.yaml")["artifacts"]}
    assert has(record_errors(doc, arts), "自己申告は不可")


def sim_docs(bundle_docs, status, refs, version=1):
    docs = copy.deepcopy(bundle_docs)
    sim = {"simulation_id": "SIM-001", "method": "tree_test", "target": {"artifact_id": "ART-004", "version": version},
           "persona": {"statement": "家族", "source": {"artifact_id": "ART-005", "version": 1}},
           "evaluator": {"host": "h", "model": "m", "context": "labels_and_prompts_only"}, "repetitions": 1,
           "categories": [], "items": [{"item_id": "TASK-01", "prompt": "南地区", "expected": ["NODE-009"],
                                        "expected_first": "NODE-006"}],
           "trials": [{"repetition": 1, "item_id": "TASK-01", "first_choice": "NODE-006", "answer": "NODE-009"}],
           "threshold": {"success_rate": 0.8}, "results": [], "classification": "internal",
           "recorded_at": "2026-10-19T10:00:00+09:00"}
    sim["results"] = derive_results(sim)
    docs["simulations.yaml"] = {"schema_version": "1.0", "run_id": "RUN-FIXTURE-001", "simulations": [sim]}
    d = next(x for x in docs["decisions.yaml"]["decisions"] if x["decision_id"] == "IA-003")
    d.update(validation_status=status, validation_record_ids=refs)
    return docs


def errors_for(docs):
    return contracts.consistency_errors(BUNDLE, docs, provenance.registered_sources())[0]


def test_simulated_requires_simulation_record(bundle_docs):
    assert errors_for(sim_docs(bundle_docs, "simulated", ["SIM-001"])) == []
    assert has(errors_for(sim_docs(bundle_docs, "simulated", [])), "模擬評価の記録（SIM-nnn）への参照が必要")
    assert has(errors_for(sim_docs(bundle_docs, "simulated", ["SIM-002"])), "模擬評価の記録 SIM-002 がない")


def test_simulation_cannot_support_tested_status(bundle_docs):
    """AC-07: 模擬評価から tested_* へは遷移させない。"""
    errors = errors_for(sim_docs(bundle_docs, "tested_supported", ["SIM-001"]))
    assert has(errors, "模擬評価 SIM-001 は tested_supported の根拠にならない")
    assert has(errors, "実ユーザー調査の記録への参照が必要")


def test_simulation_of_old_sitemap_version_is_not_applied(bundle_docs):
    docs = sim_docs(bundle_docs, "simulated", ["SIM-001"])
    docs["simulations.yaml"]["simulations"][0]["target"]["version"] = 2
    assert has(errors_for(docs), "SIM-001: target ART-004 v2 は登録済みの sitemap ではない")
