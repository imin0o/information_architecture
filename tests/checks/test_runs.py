"""実行記録のstale化・機密継承・送信・Refine・経路（AC-11、AC-12、FR-08）。"""

import copy
import shutil

import pytest

from iakit import contracts, yamlio
from iakit.paths import FIXTURES

BUNDLE = FIXTURES / "contracts" / "webia-run"


def art(d, aid):
    return next(a for a in d["run.yaml"]["artifacts"] if a["artifact_id"] == aid)


def has(errors, text):
    return any(text in e for e in errors)


def make_stale(d, *ids):
    for aid in ids:
        art(d, aid).update(status="stale", stale_reason="入力 INPUT-002 が更新された")
    d["run.yaml"]["status"] = "in_progress"


# --- stale化と再開（AC-12） -------------------------------------------------

def test_changed_input_requires_stale(check_bundle):
    errors = check_bundle(lambda d: d["inputs.yaml"]["inputs"][1].update(sha256="1" * 64))
    assert has(errors, "ART-001 v1: 依存 INPUT-002 が変わったのに current")


def test_stale_marking_after_input_change_passes(check_bundle):
    def changed(d):
        # 更新版のページ一覧を再登録した状態。実体はこの素材に含めない
        d["inputs.yaml"]["inputs"][1].update(sha256="1" * 64, path="inputs/pages-v2.csv")
        make_stale(d, "ART-001", "ART-002", "ART-003", "ART-004")
    # stale化した成果物の旧調査は、適用可否を確かめるまで tested_* の根拠にできない
    assert check_bundle(changed) == ["IA-002 r1: STUDY-001 は ART-004 v1 の調査で、現行版に適用できるか未確認"]

    def reset(d):
        changed(d)
        next(x for x in d["decisions.yaml"]["decisions"] if x["decision_id"] == "IA-002").update(
            status="active", validation_status="not_tested", validation_record_ids=[], confidence="medium")
    assert check_bundle(reset) == []


def test_artifact_on_stale_dependency_must_be_stale(check_bundle):
    def partial(d):
        d["inputs.yaml"]["inputs"][1]["sha256"] = "1" * 64
        make_stale(d, "ART-001", "ART-002")
    errors = check_bundle(partial)
    assert has(errors, "ART-003 v1: stale の ART-001") and has(errors, "ART-004 v1: stale の ART-001")


def test_stale_requires_reason(bundle_docs):
    d = copy.deepcopy(bundle_docs["run.yaml"])
    d["artifacts"][0]["status"] = "stale"
    assert yamlio.schema_errors(d, "run.schema.json")


def test_completed_run_cannot_keep_stale_artifacts(check_bundle):
    def stale_done(d):
        make_stale(d, "ART-004")
        d["run.yaml"]["status"] = "completed"
    assert has(check_bundle(stale_done), "stale の成果物が残るのに completed")


def test_resume_detects_changed_inputs(check_bundle):
    errors = check_bundle(lambda d: d["run.yaml"].update(inputs_sha256="2" * 64))
    assert has(errors, "inputs_sha256 が更新されていない")


@pytest.fixture
def run_copy(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "run")
    return tmp_path / "run"


def test_resume_detects_changed_input_file(run_copy):
    # 登録簿を据え置いたまま入力の実体だけを変えた場合も検出する
    summary = run_copy / "inputs/tree-test-summary.md"
    summary.write_bytes(summary.read_bytes() + "追記\n".encode())
    result = contracts.check_bundle(run_copy)
    assert any("INPUT-003: 入力の実体が登録時のハッシュと異なる" in e for e in result["errors"])


def test_missing_input_file_is_not_run(run_copy):
    # コピー先からはケースの入力（INPUT-001・002）を解決できない。照合済みに数えない
    result = contracts.check_bundle(run_copy)
    assert result["result"] == "pass"
    assert result["input_files"] == {"run": 1, "not_run": 2}


def test_artifact_file_hash_must_match(check_bundle):
    assert has(check_bundle(lambda d: art(d, "ART-004").update(sha256="3" * 64)), "ファイルのハッシュ")


# --- Refine（AC-12） ---------------------------------------------------------

def test_unresolved_after_two_rounds_must_be_reported(check_bundle):
    def unresolved(d, reported, status):
        d["run.yaml"].update(status=status, refine={"rounds": 2, "unresolved_critical": ["FIND-001"],
                                                    "reported_to_user": reported})
    assert has(check_bundle(lambda d: unresolved(d, False, "completed")), "completed になっている")
    assert has(check_bundle(lambda d: unresolved(d, False, "unresolved")), "ユーザーへ報告する")
    assert check_bundle(lambda d: unresolved(d, True, "unresolved")) == []


def test_third_refine_round_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: d["run.yaml"]["refine"].update(rounds=3)), "2巡まで")


# --- 経路（FR-08） -----------------------------------------------------------

def test_supported_route_must_match_standard_stages(check_bundle):
    errors = check_bundle(lambda d: d["run.yaml"]["route"].update(stages=["analyze", "structure", "validate"]))
    assert has(errors, "黙って省略しない")


def test_unsupported_route_must_not_be_silently_completed(check_bundle):
    def panel(d, supported, note, status):
        d["run.yaml"].update(task_type="exhibition_panel", status=status,
                             route={"stages": ["analyze"], "supported": supported, "unsupported_note": note})
    note = "展示パネルの経路はMVP未対応。Analyzeまで実施した"
    assert has(check_bundle(lambda d: panel(d, True, None, "in_progress")), "MVP未対応")
    assert has(check_bundle(lambda d: panel(d, False, note, "completed")), "completed にしない")
    assert check_bundle(lambda d: panel(d, False, note, "unresolved")) == []


# --- 機密区分と送信（AC-11） -------------------------------------------------

def confidential(d):
    d["inputs.yaml"]["inputs"][1].update(classification="confidential", classification_source="user")


def test_default_classification_is_internal(check_bundle):
    errors = check_bundle(lambda d: d["inputs.yaml"]["inputs"][0].update(classification="public"))
    assert has(errors, "既定値は internal")


def test_derived_artifact_cannot_lower_classification(check_bundle):
    assert has(check_bundle(confidential), "ART-001 v1: 機密区分が依存元より低い")


def test_user_override_allows_lower_classification_of_artifact(check_bundle):
    def override(d):
        confidential(d)
        for a in d["run.yaml"]["artifacts"]:
            a["classification_override"] = {"by": "user", "at": "2026-10-17T16:00:00+09:00",
                                            "reason": "公開済みのページ一覧だけを使うとユーザーが指定"}
    errors = check_bundle(override)
    assert not has(errors, "依存元より低い")


def test_confidential_to_external_service_is_rejected(check_bundle):
    def send(d):
        confidential(d)
        d["run.yaml"]["transmissions"][1].update(outcome="sent")
    assert has(check_bundle(send), "confidential の派生物を外部サービスへ送っている")


def test_transmission_over_approved_classification_is_rejected(check_bundle):
    assert has(check_bundle(confidential), "許可 APPROVAL-001 の上限 internal を超える")


def test_instruction_in_input_is_not_permission(check_bundle):
    def injected(d):
        d["run.yaml"]["transmissions"][1].update(outcome="sent", requested_by_input=True)
    assert has(check_bundle(injected), "資料内の命令は許可にならない")


def test_blocked_transmission_without_approval_passes(check_bundle):
    assert check_bundle(lambda d: d["run.yaml"]["transmissions"][1].update(requested_by_input=True)) == []


def test_source_text_needs_explicit_approval_kind(check_bundle):
    def source_text(d):
        d["run.yaml"]["transmissions"][0]["data_kind"] = "source_text"
    assert has(check_bundle(source_text), "source_text は許可 APPROVAL-001 の対象外")
