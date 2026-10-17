"""判断記録の状態・置換・confidence・実測の区別（FR-07、AC-04、AC-07）。"""

import copy

import pytest

from iakit import decisions, yamlio


def dec(d, did, rev=None):
    return next(x for x in d["decisions.yaml"]["decisions"]
                if x["decision_id"] == did and (rev is None or x["decision_revision"] == rev))


def study(d):
    return d["observations.yaml"]["studies"][0]


def add_study(d, sid, result):
    s = copy.deepcopy(study(d))
    s.update(study_id=sid, result=result)
    d["observations.yaml"]["studies"].append(s)


def has(errors, text):
    return any(text in e for e in errors)


# --- 置換関係 ---------------------------------------------------------------

def test_deleting_old_revision_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: d["decisions.yaml"]["decisions"].pop(0)), "連番")


def test_old_revision_must_be_superseded(check_bundle):
    assert has(check_bundle(lambda d: dec(d, "IA-001", 1).update(status="active")), "新しい版があるのに")


def test_supersedes_must_point_to_previous_revision(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-001", 2).update(supersedes={"decision_id": "IA-002", "decision_revision": 1}))
    assert has(errors, "直前版")


def test_withdrawn_decision_is_allowed_but_not_current(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-003").update(status="withdrawn"))
    assert errors and all("現行でない判断 IA-003" in e for e in errors)


def test_artifact_referencing_old_revision_is_rejected(check_bundle):
    def old(d):
        d["run.yaml"]["artifacts"][2]["decision_refs"] = [{"decision_id": "IA-001", "decision_revision": 1}]
    assert has(check_bundle(old), "現行でない判断 IA-001 r1")


# --- 代替案 -----------------------------------------------------------------

def test_empty_alternatives_require_reason(bundle_docs):
    d = copy.deepcopy(bundle_docs["decisions.yaml"])
    d["decisions"][3]["alternatives_not_applicable_reason"] = None
    assert yamlio.schema_errors(d, "decision.schema.json")


def test_reason_with_alternatives_is_rejected(bundle_docs):
    d = copy.deepcopy(bundle_docs["decisions.yaml"])
    d["decisions"][1]["alternatives_not_applicable_reason"] = "なし"
    assert yamlio.schema_errors(d, "decision.schema.json")


# --- 文献根拠と反証 ---------------------------------------------------------

def test_not_supported_claim_cannot_be_primary(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-001", 2)["sources"][1].update(primary=True))
    assert has(errors, "採用理由（primary）")


def test_not_supported_claim_must_be_counter_evidence(check_bundle):
    assert has(check_bundle(lambda d: dec(d, "IA-001", 2).update(counter_evidence=[])), "counter_evidence に挙げていない")


def test_unresolved_counter_evidence_forces_low(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-001", 2)["counter_evidence"][0].update(resolved=False))
    assert has(errors, "confidence が medium だが判定基準からの導出は low")


def test_paraphrased_claim_is_rejected(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-001", 2)["sources"][0].update(claim="前付けはローマ数字で数える"))
    assert has(errors, "登録と異なる")


def test_unverified_primary_source_forces_low(check_bundle):
    def unverified(d):
        d["receipts.yaml"]["receipts"][0]["human_checks"]["source_location"] = {
            "result": "not_done", "checked_by": None, "checked_at": None}
        for s in dec(d, "IA-001", 2)["sources"]:
            s["source_verification"] = "unverified"
    assert has(check_bundle(unverified), "導出は low")


# --- confidence ------------------------------------------------------------

def test_critical_assumption_forces_low(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-001", 2)["assumptions"][0].update(critical=True))
    assert has(errors, "導出は low")


def test_hearing_answer_replaces_assumption(check_bundle):
    # 質問への回答が得られた場合は入力として登録し、仮定ではなく観察として扱う
    def answered(d):
        d["inputs.yaml"]["inputs"].append({
            "input_id": "INPUT-004", "kind": "hearing_answer", "format": "text", "description": "利用環境の回答",
            "received_at": "2026-10-17T10:45:00+09:00", "path": "inputs/hearing-004.txt", "sha256": "4" * 64,
            "classification": "internal", "classification_source": "default", "unreadable": []})
        dec(d, "IA-001", 2)["assumptions"] = []
        dec(d, "IA-001", 2)["input_observations"].append({
            "observation_id": "OBS-005", "input_id": "INPUT-004", "locator": "hearing-004.txt#L1",
            "observation": "利用者の多くはスマートフォンで閲覧する"})
    assert has(check_bundle(answered), "依存 INPUT-004 が depends_on にない")

    def regenerated(d):
        answered(d)
        ref = {"decision_id": "IA-001", "decision_revision": 2}
        for a in d["run.yaml"]["artifacts"]:
            if ref in a["decision_refs"]:
                a["depends_on"].append({"ref": "INPUT-004", "sha256": "4" * 64})
    assert check_bundle(regenerated) == []


def test_decision_without_observation_is_low(check_bundle):
    assert has(check_bundle(lambda d: dec(d, "IA-003").update(input_observations=[])), "導出は low")


def test_declared_high_without_study_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: dec(d, "IA-003").update(confidence="high")), "導出は medium")


@pytest.mark.parametrize(("status", "expected"), [
    ("not_tested", "medium"), ("planned", "medium"), ("simulated", "medium"),
    ("tested_supported", "high"), ("tested_refuted", "low"), ("tested_inconclusive", "low"),
])
def test_confidence_follows_validation_status(bundle_docs, status, expected):
    d = copy.deepcopy(dec(bundle_docs, "IA-002"))
    d["validation_status"] = status
    assert decisions.derive_confidence(d, {}) == expected


# --- 実測と推定（AC-07） ----------------------------------------------------

def test_tested_status_requires_study_record(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-003").update(validation_status="tested_supported", confidence="high"))
    assert has(errors, "実ユーザー調査の記録への参照が必要")


@pytest.mark.parametrize("method", ["llm_simulation", "expert_review", "heuristic_review"])
def test_simulated_review_is_not_a_study(bundle_docs, method):
    d = copy.deepcopy(bundle_docs["observations.yaml"])
    d["studies"][0]["method"] = method
    assert yamlio.schema_errors(d, "observation.schema.json")


def test_simulated_status_cannot_cite_study(check_bundle):
    errors = check_bundle(lambda d: dec(d, "IA-002").update(validation_status="simulated", confidence="medium"))
    assert has(errors, "調査記録を参照しながら simulated")


def test_refuted_study_forces_low(check_bundle):
    def refuted(d):
        study(d)["result"] = "refuted"
        dec(d, "IA-002").update(validation_status="tested_refuted")
    assert has(check_bundle(refuted), "導出は low")

    def refuted_low(d):
        refuted(d)
        dec(d, "IA-002")["confidence"] = "low"
    assert check_bundle(refuted_low) == []


def test_refuted_study_cannot_be_reported_as_supported(check_bundle):
    assert has(check_bundle(lambda d: study(d).update(result="refuted")), "導出は tested_refuted")


def test_mixed_results_are_inconclusive(check_bundle):
    def mixed(d):
        add_study(d, "STUDY-002", "refuted")
        dec(d, "IA-002")["validation_record_ids"].append("STUDY-002")
    assert has(check_bundle(mixed), "導出は tested_inconclusive")

    def reported(d):
        mixed(d)
        dec(d, "IA-002").update(validation_status="tested_inconclusive", confidence="low")
    assert check_bundle(reported) == []


def test_study_of_old_artifact_version_is_not_applied(check_bundle):
    errors = check_bundle(lambda d: study(d)["target"].update(version=2))
    assert has(errors, "現行版に適用できるか未確認")


# --- 機密区分の継承 ---------------------------------------------------------

def test_decision_inherits_input_classification(check_bundle):
    errors = check_bundle(lambda d: d["inputs.yaml"]["inputs"][1].update(
        classification="confidential", classification_source="user"))
    assert has(errors, "IA-001 r2: 機密区分 internal が使用した入力")


@pytest.mark.parametrize(("input_id", "message"), [("INPUT-999", "登録されていない"),
                                                  ("INPUT-001", "research_record ではない")])
def test_study_requires_registered_research_input(check_bundle, input_id, message):
    errors = check_bundle(lambda d: study(d).update(input_id=input_id))
    assert has(errors, message)
    assert has(errors, "IA-002 r1: 調査記録 STUDY-001 がない")


def test_study_cannot_lower_source_classification(check_bundle):
    def confidential_research(d):
        d["inputs.yaml"]["inputs"][2].update(classification="confidential", classification_source="user")
    errors = check_bundle(confidential_research)
    assert has(errors, "STUDY-001: 調査の機密区分が入力 INPUT-003 より低い")
    assert has(errors, "IA-002 r1: 調査記録 STUDY-001 がない")


def test_decision_inherits_valid_study_classification(check_bundle):
    errors = check_bundle(lambda d: study(d).update(classification="confidential"))
    assert has(errors, "IA-002 r1: 機密区分 internal が使用した入力・調査記録の区分より低い")


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_study_id_cannot_hide_refutation(check_bundle, reverse):
    def duplicate(d):
        refuted = copy.deepcopy(study(d))
        refuted["result"] = "refuted"
        d["observations.yaml"]["studies"].insert(1 if reverse else 0, refuted)
    errors = check_bundle(duplicate)
    assert has(errors, "STUDY-001: study_id が重複している")
    assert has(errors, "IA-002 r1: 調査記録 STUDY-001 がない")


def test_unreferenced_study_is_also_checked(check_bundle):
    def invalid(d):
        add_study(d, "STUDY-002", "supported")
        d["observations.yaml"]["studies"][1]["input_id"] = "INPUT-999"
    assert has(check_bundle(invalid), "STUDY-002: 調査の入力 INPUT-999 が登録されていない")


@pytest.mark.parametrize("target", ["CONTENT-999", "CONCEPT-999", "NODE-999", "SCHEME-999", "ART-999"])
def test_decision_target_must_exist(check_bundle, target):
    errors = check_bundle(lambda d: dec(d, "IA-003").update(target_ids=[target]))
    assert has(errors, f"対象 {target} が登録されていない")


@pytest.mark.parametrize("revision", [1, 2])
def test_decision_run_id_must_match_file_including_history(check_bundle, revision):
    errors = check_bundle(lambda d: dec(d, "IA-001", revision).update(run_id="RUN-OTHER"))
    assert has(errors, f"IA-001 r{revision}: run_id が判断記録のファイルと一致しない")


def test_superseded_target_is_not_checked_against_current_artifacts(check_bundle):
    assert check_bundle(lambda d: dec(d, "IA-001", 1).update(target_ids=["SCHEME-999"])) == []


@pytest.mark.parametrize("target", ["CONTENT-011", "CONCEPT-001", "NODE-002", "SCHEME-001", "ART-004"])
def test_existing_decision_target_types_are_supported(check_bundle, target):
    assert check_bundle(lambda d: dec(d, "IA-003").update(target_ids=[target])) == []
