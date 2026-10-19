"""模擬評価の記録（P2-2、P2-3）と、simulated・tested_* の区別（AC-07）。"""

import copy
import shutil

import pytest

from iakit import contracts, decision_cmd, manifest, provenance, run_cmd, simulation_cmd, yamlio
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


def test_simulation_classification_cannot_be_lowered(run_dir, bundle_docs):
    before = (run_dir / "run.yaml").read_bytes()
    with pytest.raises(ValueError, match="機密区分"):
        simulation_cmd.add(run_dir, dict(tree_test(), classification="public"))
    assert not (run_dir / "simulations.yaml").exists()
    assert (run_dir / "run.yaml").read_bytes() == before
    docs = sim_docs(bundle_docs, "simulated", ["SIM-001"])
    docs["simulations.yaml"]["simulations"][0]["classification"] = "confidential"
    assert has(errors_for(docs), "機密区分 internal が使用した入力・調査記録の区分より低い")


def test_simulated_decision_inherits_classification_and_rejects_override(run_dir):
    simulation_cmd.add(run_dir, dict(tree_test(), classification="confidential"))
    before = (run_dir / "decisions.yaml").read_bytes()
    changes = {"validation_status": "simulated", "validation_record_ids": ["SIM-001"]}
    with pytest.raises(ValueError, match="機密区分"):
        decision_cmd.revise(run_dir, "IA-003", dict(changes, classification="internal"))
    assert (run_dir / "decisions.yaml").read_bytes() == before
    assert decision_cmd.revise(run_dir, "IA-003", changes)["classification"] == "confidential"


@pytest.mark.parametrize("field,value", [("expected", ["NODE-002"]), ("expected_first", "NODE-002"),
                                         ("expected_first_choices", ["NODE-002"])])
def test_contract_rederives_ground_truth_even_if_results_are_consistent(run_dir, field, value):
    simulation_cmd.add(run_dir, tree_test(answers=("NODE-002",)))
    path = run_dir / "simulations.yaml"
    doc = yamlio.load(path)
    sim = doc["simulations"][0]
    sim["items"][0][field] = value
    sim["results"] = derive_results(sim)
    path.write_text(yamlio.dumps(doc), encoding="utf-8")
    assert has(contracts.check_bundle(run_dir)["errors"], "正解が対象版のサイトマップからの導出と一致しない")


def test_snapshot_tampering_and_historical_ground_truth(run_dir):
    simulation_cmd.add(run_dir, tree_test())
    doc = yamlio.load(run_dir / "simulations.yaml")
    sim = doc["simulations"][0]
    arts = {a["artifact_id"]: a for a in yamlio.load(run_dir / "run.yaml")["artifacts"]}
    assert sim["sitemap_sha256"] == manifest.sha256_file(run_dir / "sitemap.yaml")
    changed = copy.deepcopy(doc)
    changed["simulations"][0]["sitemap_snapshot"] += "\n# changed\n"
    assert has(record_errors(changed, arts), "ハッシュが一致しない")
    arts["ART-004"]["version"] += 1
    arts["ART-004"]["sha256"] = "0" * 64
    assert record_errors(doc, arts) == []
    sim["items"][0]["expected"] = ["NODE-002"]
    sim["results"] = derive_results(sim)
    assert has(record_errors(doc, arts), "正解が対象版")


def test_content_targets_accept_all_destinations_and_first_branches(run_dir):
    path = run_dir / "sitemap.yaml"
    site = yamlio.load(path)
    task = next(t for t in site["tasks"] if t["task_id"] == "TASK-03")
    task["target_content_ids"] = ["CONTENT-002"]
    path.write_text(yamlio.dumps(site), encoding="utf-8")
    run_cmd.artifact(run_dir, "sitemap.yaml", "sitemap", [], [])
    data = tree_test(answers=("NODE-003", "NODE-007"))
    data["items"] = [{"item_id": "TASK-03", "prompt": "北地区で家の仕事を頼む"}]
    data["trials"] = [{"repetition": n, "item_id": "TASK-03", "first_choice": first, "answer": answer}
                      for n, first, answer in ((1, "NODE-002", "NODE-003"), (2, "NODE-006", "NODE-007"))]
    out = simulation_cmd.add(run_dir, data)
    assert out["results"][0]["success_rate"] == out["results"][0]["direct_first_rate"] == 1.0
    sim = yamlio.load(run_dir / "simulations.yaml")["simulations"][0]
    assert sim["items"][0]["expected"] == ["NODE-003", "NODE-007"]
    assert sim["items"][0]["expected_first_choices"] == ["NODE-002", "NODE-006"]
    kit = simulation_cmd.kit(run_dir, "tree_test")
    assert "CONTENT-002" not in str(kit) and "expected" not in str(kit)


def test_target_content_must_exist_and_be_at_path_destination(bundle_docs):
    from iakit.structure import sitemap_errors
    site = copy.deepcopy(bundle_docs["sitemap.yaml"])
    site["tasks"][0]["target_content_ids"] = ["CONTENT-002", "CONTENT-999"]
    found = sitemap_errors(site, bundle_docs["content-inventory.yaml"], bundle_docs["controlled-vocabulary.yaml"])
    assert has(found, "目的のコンテンツ CONTENT-999") and has(found, "経路の末端が目的のコンテンツ")
