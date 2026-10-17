"""出典照合の正常例・誤り例（付録B.4、AC-05）。自作PDFの固定テキストだけを使う。"""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from iakit import provenance, provenance_local, snapshot, yamlio
from iakit.paths import FIXTURES, SCHEMAS

PDF = FIXTURES / "pdf/page-labels.pdf"
PDF_SHA = "abafc5921ea3ae879e2bc93b54bdea7c4d16a066378cdfe2a86710630d6822bb"


def record(docs, ev):
    return next(r for r in docs["provenance.yaml"]["records"] if r["evidence_id"] == ev)


def review(docs, claim):
    return next(rv for r in docs["provenance.yaml"]["records"] for rv in r["support_reviews"] if rv["claim_id"] == claim)


def derived(docs):
    return provenance.derive(docs["provenance.yaml"], docs["receipts.yaml"])


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*.schema.json")), ids=lambda p: p.name)
def test_schemas_are_valid_draft_2020_12_with_matching_id(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == path.name


def test_cross_file_reference_is_resolved_inside_schemas():
    errors = yamlio.schema_errors({"schema_version": "1.0", "records": [{"evidence_id": "bad"}]}, "provenance.schema.json")
    assert errors and not any("Unresolvable" in e for e in errors)


def test_valid_bundle_passes(check_bundle):
    assert check_bundle() == []


def test_derived_states_separate_existence_and_support(bundle_docs):
    states = derived(bundle_docs)["states"]
    assert states[("EV-0001", "CLAIM-0001")] == {"source_verification": "verified", "support_status": "supported"}
    # 実在する抜粋に無関係な主張: ハッシュ検査は通り、人のレビューで不支持
    assert states[("EV-0001", "CLAIM-0002")] == {"source_verification": "verified", "support_status": "not_supported"}
    # 照合記録がない根拠は検証済みにならない
    assert states[("EV-0002", "CLAIM-0003")] == {"source_verification": "unverified", "support_status": "unreviewed"}


@pytest.mark.parametrize("change", [
    lambda d: d["decisions.yaml"]["decisions"][1]["sources"][0].update(claim_id="CLAIM-0999"),
    lambda d: d["decisions.yaml"]["decisions"][1]["sources"][0].update(evidence_id="EV-0002"),
    lambda d: d["decisions.yaml"]["decisions"][1]["sources"][0].update(source_id="BOOK-001"),
    lambda d: record(d, "EV-0002").update(source_id="FIXTURE-reflowable"),
    lambda d: record(d, "EV-0001").update(source_id="BOOK-099"),
    lambda d: d["receipts.yaml"]["receipts"][0].update(evidence_id="EV-0002"),
], ids=["unknown-claim", "claim-of-other-evidence", "other-source", "other-fixture", "unregistered", "receipt-target"])
def test_reference_mismatch_is_rejected(check_bundle, change):
    assert check_bundle(change)


def test_source_hash_must_match_registered_original(check_bundle):
    errors = check_bundle(lambda d: record(d, "EV-0001").update(source_sha256="0" * 64))
    assert any("登録簿の原典ハッシュ" in e for e in errors)


def test_changed_claim_text_requires_new_review(check_bundle):
    errors = check_bundle(lambda d: review(d, "CLAIM-0001").update(claim="前付けには必ずローマ数字を使う"))
    assert any("新規レビュー" in e for e in errors)


def test_changed_applicability_invalidates_review(bundle_docs, check_bundle):
    d = copy.deepcopy(bundle_docs)
    review(d, "CLAIM-0001")["applicability"] = "すべての文書"
    assert derived(d)["states"][("EV-0001", "CLAIM-0001")]["support_status"] == "unreviewed"
    errors = check_bundle(lambda d: review(d, "CLAIM-0001").update(applicability="すべての文書"))
    assert any("support_status" in e and "自己申告" in e for e in errors)


def test_changed_record_invalidates_receipt(bundle_docs):
    d = copy.deepcopy(bundle_docs)
    record(d, "EV-0001")["locator"]["chapter"] = "1"
    result = derived(d)
    assert result["stale_receipts"] == {"VR-0001": ["登録レコードが照合後に変わった"]}
    assert result["states"][("EV-0001", "CLAIM-0001")]["source_verification"] == "unverified"


def test_changed_knowledge_file_invalidates_receipt(bundle_docs, tmp_path):
    d = copy.deepcopy(bundle_docs)
    (tmp_path / "method.md").write_bytes("reviewed\n".encode())
    d["receipts.yaml"]["receipts"][0]["targets"]["knowledge_files"] = [
        {"path": "method.md", "sha256": provenance.text_sha256("reviewed\n")}]
    assert provenance.derive(d["provenance.yaml"], d["receipts.yaml"], tmp_path)["stale_receipts"] == {}
    (tmp_path / "method.md").write_bytes("changed after review\n".encode())
    assert provenance.derive(d["provenance.yaml"], d["receipts.yaml"], tmp_path)["stale_receipts"]


@pytest.mark.parametrize("checks", [
    {"human_checks": {"source_location": {"result": "not_done", "checked_by": None, "checked_at": None}}},
    {"machine_checks": {"reference": "pass", "excerpt": "not_run", "original_hash": "pass"}},
    {"machine_checks": {"reference": "pass", "excerpt": "pass", "original_hash": "not_run"}},
], ids=["no-human-check", "excerpt-not-run", "original-not-run"])
def test_incomplete_receipt_does_not_verify(bundle_docs, checks):
    # 文字列検索の候補やCIだけの検査では verified にしない
    d = copy.deepcopy(bundle_docs)
    d["receipts.yaml"]["receipts"][0].update(checks)
    assert derived(d)["states"][("EV-0001", "CLAIM-0001")]["source_verification"] == "unverified"


def test_runtime_self_declared_verified_is_rejected(check_bundle):
    def declare(d):
        d["decisions.yaml"]["decisions"][1]["sources"].append({
            "source_id": "FIXTURE-page-labels", "evidence_id": "EV-0002", "claim_id": "CLAIM-0003",
            "claim": "本文のページ番号は1から振り直す", "primary": False,
            "source_verification": "verified", "support_status": "supported"})
    errors = check_bundle(declare)
    assert any("CLAIM-0003 の source_verification" in e for e in errors)
    assert any("CLAIM-0003 の support_status" in e for e in errors)


@pytest.mark.parametrize("change", [
    {"printed_page_labels": ["1"]},
    {"pdf_page_indices": [1]},
    {"printed_page_labels": [], "pdf_page_indices": [], "epub_href": "text/ch1.xhtml"},
], ids=["page-index-as-label", "wrong-page", "epub-on-pdf"])
def test_wrong_locator_is_rejected(bundle_docs, tmp_path, change):
    snapshot.create(PDF, "FIXTURE-page-labels", tmp_path, "trial")
    d = copy.deepcopy(bundle_docs["provenance.yaml"])
    d["records"][0]["locator"].update(change)
    errors, _ = provenance_local.local_results(d, tmp_path, {})
    assert errors


@pytest.fixture
def snap(tmp_path):
    directory = snapshot.create(PDF, "FIXTURE-page-labels", tmp_path, "trial")
    assert directory.name == "EXTRACT-FX-001"
    return tmp_path


def test_local_check_passes_and_counts_runs(bundle_docs, snap):
    errors, counts = provenance_local.local_results(bundle_docs["provenance.yaml"], snap, {PDF_SHA: PDF})
    assert errors == []
    assert counts == {"excerpt": {"run": 2, "not_run": 0}, "original_hash": {"run": 2, "not_run": 0}}


def test_local_check_without_snapshot_is_not_run(bundle_docs, tmp_path):
    errors, counts = provenance_local.local_results(bundle_docs["provenance.yaml"], tmp_path, {})
    assert errors == []
    assert counts["excerpt"] == {"run": 0, "not_run": 2}


def test_missing_original_is_not_run_not_pass(bundle_docs, snap):
    _, counts = provenance_local.local_results(bundle_docs["provenance.yaml"], snap, {})
    assert counts["original_hash"] == {"run": 0, "not_run": 2}


@pytest.mark.parametrize("change", [
    {"excerpt_sha256": "0" * 64},
    {"excerpt_preview": "Front matter uses arabic page numbers."},
    {"excerpt_preview": "matter uses roman page numbers."},
    {"extracted_sha256": "0" * 64},
    {"source_sha256": "0" * 64},
    {"extracted_ref": {"extraction_id": "EXTRACT-FX-001", "start_line": 4, "end_line": 99}},
    {"extracted_ref": {"extraction_id": "EXTRACT-FX-001", "start_line": 4, "end_line": 6}},
], ids=["excerpt-hash", "preview-changed", "preview-not-prefix", "extracted-hash", "source-hash", "range-invalid",
        "range-changed"])
def test_local_check_detects_excerpt_mismatch(bundle_docs, snap, change):
    d = copy.deepcopy(bundle_docs["provenance.yaml"])
    d["records"][0].update(change)
    errors, _ = provenance_local.local_results(d, snap, {})
    assert errors


def test_local_check_detects_replaced_extraction_text(bundle_docs, snap):
    text = snap / "EXTRACT-FX-001" / "text.txt"
    text.write_bytes(text.read_bytes().replace(b"roman", b"Roman"))
    errors, _ = provenance_local.local_results(bundle_docs["provenance.yaml"], snap, {})
    assert any("text.txt のハッシュ" in e for e in errors)


def test_unrelated_claim_passes_hash_checks(bundle_docs, snap, check_bundle):
    # 限界例: ハッシュ・参照の検査は通る。不支持の判定は人のレビュー記録にだけ残る
    errors, _ = provenance_local.local_results(bundle_docs["provenance.yaml"], snap, {PDF_SHA: PDF})
    assert errors == [] and check_bundle() == []
    assert review(bundle_docs, "CLAIM-0002")["status"] == "not_supported"


@pytest.mark.parametrize(("status", "by", "at"), [
    ("supported", None, None),
    ("not_supported", "Reviewer", None),
    ("unreviewed", "Reviewer", "2026-10-17T12:00:00+09:00"),
])
def test_review_status_and_reviewer_must_agree(bundle_docs, status, by, at):
    d = copy.deepcopy(bundle_docs["provenance.yaml"])
    d["records"][1]["support_reviews"][0].update(status=status, reviewed_by=by, reviewed_at=at)
    assert yamlio.schema_errors(d, "provenance.schema.json")
