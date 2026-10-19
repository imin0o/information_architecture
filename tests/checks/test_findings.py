"""評価の指摘とRefineの整合、Markdown成果物の判断参照（FR-06、FR-08、IN-04）。"""

from iakit import yamlio
import pytest


def has(errors, text):
    return any(text in e for e in errors)


def finding(d):
    return d["findings.yaml"]["findings"][0]


def test_bundle_findings_pass(check_bundle):
    assert check_bundle() == []


def artifact(d, kind):
    return next(a for a in d["run.yaml"]["artifacts"] if a["kind"] == kind)


def test_yaml_artifact_must_show_assumed_decision_in_header(check_bundle):
    ref = {"decision_id": "IA-001", "decision_revision": 2}
    errors = check_bundle(lambda d: artifact(d, "sitemap")["decision_refs"].append(ref))
    assert has(errors, "仮定を持つ判断 IA-001 r2 を先頭のコメントに示していない")


def test_markdown_artifact_must_show_assumed_decision_in_first_section(check_bundle):
    ref = {"decision_id": "IA-001", "decision_revision": 2}
    errors = check_bundle(lambda d: artifact(d, "information_architecture")["decision_refs"].append(ref))
    assert has(errors, "仮定を持つ判断 IA-001 r2 を最初の節に示していない")


def test_decision_without_assumptions_needs_no_header(check_bundle):
    ref = {"decision_id": "IA-003", "decision_revision": 1}
    assert not has(check_bundle(lambda d: artifact(d, "sitemap")["decision_refs"].append(ref)), "仮定を持つ判断")


def test_open_critical_must_be_listed_in_refine(check_bundle):
    errors = check_bundle(lambda d: finding(d).update(severity="critical"))
    assert has(errors, "refine.unresolved_critical [] が指摘の状態 ['FIND-001'] と一致しない")


def test_completed_run_with_open_critical_is_rejected(check_bundle):
    def critical(d):
        finding(d).update(severity="critical")
        d["run.yaml"]["refine"]["unresolved_critical"] = ["FIND-001"]
    assert has(check_bundle(critical), "未解決の重大な指摘が残るのに completed")


def test_unresolved_critical_before_second_round_is_rejected(check_bundle):
    def early(d):
        finding(d).update(severity="critical", status="unresolved")
        d["run.yaml"]["refine"]["unresolved_critical"] = ["FIND-001"]
        d["run.yaml"]["status"] = "in_progress"
    assert has(check_bundle(early), "2巡に達する前の重大な指摘は open のまま")


def test_resolved_requires_newer_registered_version(check_bundle):
    assert has(check_bundle(lambda d: finding(d).update(status="resolved", resolved_in={"artifact_id": "ART-004", "version": 1})),
               "resolved_in は対象と同じ成果物の、指摘より新しい")
    assert has(check_bundle(lambda d: finding(d).update(status="resolved", resolved_in={"artifact_id": "ART-004", "version": 2})),
               "resolved_in は")


def test_finding_target_and_round_must_exist(check_bundle):
    assert has(check_bundle(lambda d: finding(d)["target"].update(version=3)), "対象 ART-004 v3 が実行記録にない")
    assert has(check_bundle(lambda d: finding(d).update(round=2)), "round 2 が実行記録の巡数 1 を超える")


def test_duplicate_finding_ids_are_rejected(check_bundle):
    assert has(check_bundle(lambda d: d["findings.yaml"]["findings"].append(dict(finding(d)))), "finding_id が重複")


def test_resolved_without_version_fails_schema(bundle_docs):
    doc = dict(bundle_docs["findings.yaml"])
    doc["findings"] = [dict(doc["findings"][0], status="resolved", resolved_in=None)]
    assert yamlio.schema_errors(doc, "findings.schema.json")


def test_findings_must_match_registration(check_bundle):
    assert has(check_bundle(lambda d: d["findings.yaml"].update(version=2)), "review_findings と一致しない")


def test_completed_route_requires_validation_outputs(check_bundle):
    def drop(d):
        d["run.yaml"]["artifacts"] = [a for a in d["run.yaml"]["artifacts"] if a["kind"] != "validation_plan"]
    assert has(check_bundle(drop), "段階 validate の成果物 validation_plan がない")


def test_planned_decision_must_appear_in_validation_plan(check_bundle):
    def planned(d):
        ia3 = next(x for x in d["decisions.yaml"]["decisions"] if x["decision_id"] == "IA-003")
        ia3["validation_status"] = "planned"
    assert has(check_bundle(planned), "planned の判断 IA-003 r1 に検証方法が対応付いていない")


@pytest.mark.parametrize("basis", [["SIM-001"], ["SIM-001 TASK-03"], ["SIM-001", "到達率が低い"]])
def test_simulation_only_critical_is_rejected_on_write_and_registration(tmp_path, basis):
    import shutil
    from conftest import BUNDLE
    from iakit import contracts, finding_cmd, run_cmd
    directory = tmp_path / "run"
    shutil.copytree(BUNDLE, directory)
    path = directory / "findings.yaml"
    doc = yamlio.load(path)
    data = {k: v for k, v in doc["findings"][0].items() if k not in finding_cmd.AUTO}
    data.update(severity="critical", basis=basis)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="模擬評価だけを根拠に critical"):
        finding_cmd.add(directory, data)
    assert path.read_bytes() == before
    doc["findings"][0].update(severity="critical", basis=basis)
    path.write_text(yamlio.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="模擬評価だけを根拠に critical"):
        run_cmd.artifact(directory, "findings.yaml", "review_findings", [], [])
    assert has(contracts.check_bundle(directory)["errors"], "模擬評価だけを根拠に critical")


def test_simulation_can_accompany_independent_evidence_for_critical(bundle_docs):
    from iakit.run_outputs import finding_item_errors
    import copy
    doc = copy.deepcopy(bundle_docs["findings.yaml"])
    doc["findings"][0].update(severity="critical", basis=["SIM-001", "measurements/structure.yaml: 到達不能"])
    assert finding_item_errors(doc, bundle_docs["run.yaml"]) == []
