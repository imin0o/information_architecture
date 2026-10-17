"""人の判定の記録（iakit review）。自作PDFの固定テキストと契約素材の出典登録簿だけを使う。"""

import pytest

from iakit import extract_cmd, library, manifest, provenance, review, snapshot, yamlio
from iakit.paths import FIXTURES

RUN = FIXTURES / "contracts/webia-run"
PDF = FIXTURES / "pdf/page-labels.pdf"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_cmd, "_originals_by_hash", lambda: {manifest.sha256_file(PDF): PDF})
    snapshot.create(PDF, "FIXTURE-page-labels", tmp_path / "extracted", "trial")
    prov = yamlio.load(RUN / "provenance.yaml")
    for r in prov["records"]:
        r["locator"].update(pdf_page_indices=[], printed_page_labels=[])
        for rv in r["support_reviews"]:
            rv.update(status="unreviewed", reviewed_by=None, reviewed_at=None)
    files = {
        review.PROV: "# 見出し\n" + yamlio.dumps(prov),
        review.INDEX: yamlio.dumps({"schema_version": "1.0", "needs": [], "entries": [
            {"knowledge_id": "KN-001", "kind": "method", "title": "例", "path": "library/methods/a.md",
             "claims": ["CLAIM-0001", "CLAIM-0003"], "terms": [], "conflicts": []}]}),
        "library/methods/a.md": "# 例\n\n[CLAIM-0001] [CLAIM-0003]\n",
        review.SAMPLES: yamlio.dumps({"schema_version": "1.0", "reviews": [{
            "extraction_id": "EXTRACT-FX-001", "checked_by": None, "checked_at": None, "items": [
                {"kind": "body", "pdf_page_indices": [2], "epub_href": None, "look_for": "本文", "result": None,
                 "note": None},
                {"kind": "table", "pdf_page_indices": [4], "epub_href": None, "look_for": "表", "result": None,
                 "note": None}]}]}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8", newline="\n")
    return tmp_path


def record(root, *args, **kw):
    return review.record(*args, root=root, extracted_root=root / "extracted", **kw)


def states(root):
    prov = yamlio.load(root / review.PROV)
    return provenance.derive(prov, yamlio.load(root / review.RECEIPTS), root, root / "extracted")["states"]


def test_sheet_is_local_and_lists_pending(root):
    r = review.sheet(root, root / "extracted")
    assert r["result"] == "pass" and r["evidence"] == 2 and r["sample_items"] == 2
    assert r["sheet"].startswith("sources/")
    text = (root / review.SHEET).read_text(encoding="utf-8")
    assert "Front matter uses roman page numbers." in text and "CLAIM-0002" in text
    assert "body" not in r and "Front matter" not in yamlio.dumps(r)


def test_all_ok_records_everything_with_one_command(root):
    r = record(root, "確認者", all_ok=True)
    assert r["result"] == "pass", r["errors"]
    assert r["receipts_added"] == ["VR-0001", "VR-0002"] and r["sample_items"] == 2
    assert all(s == {"source_verification": "verified", "support_status": "supported"} for s in states(root).values())
    prov = yamlio.load(root / review.PROV)
    assert prov["records"][0]["locator"]["pdf_page_indices"] == [2]
    assert (root / review.PROV).read_text(encoding="utf-8").startswith("# 見出し\n")
    receipt = yamlio.load(root / review.RECEIPTS)["receipts"][0]
    assert receipt["targets"]["knowledge_files"][0]["path"] == "library/methods/a.md"
    assert yamlio.load(root / review.SAMPLES)["reviews"][0]["items"][0]["result"] == "ok"
    assert review.sheet(root, root / "extracted")["evidence"] == 0


def test_exceptions_override_defaults(root):
    r = record(root, "確認者", all_ok=True, rejects=["EV-0002"], claim_args=["CLAIM-0002=not_supported"],
               issues=["EXTRACT-FX-001:table:列の順序が乱れる"])
    assert r["result"] == "pass", r["errors"]
    s = states(root)
    assert s[("EV-0001", "CLAIM-0002")]["support_status"] == "not_supported"
    assert s[("EV-0002", "CLAIM-0003")] == {"source_verification": "unverified", "support_status": "unreviewed"}
    items = yamlio.load(root / review.SAMPLES)["reviews"][0]["items"]
    assert [(i["result"], i["note"]) for i in items] == [("ok", None), ("issue", "列の順序が乱れる")]


def test_original_hash_not_run_does_not_verify(root, monkeypatch):
    monkeypatch.setattr(extract_cmd, "_originals_by_hash", lambda: {})
    assert record(root, "確認者", all_ok=True)["result"] == "pass"
    assert all(s["source_verification"] == "unverified" for s in states(root).values())


def test_nothing_is_recorded_without_a_judgement(root):
    r = record(root, "確認者")
    assert r["result"] == "pass" and r["receipts_added"] == [] and r["sample_items"] == 0
    assert all(s["support_status"] == "unreviewed" for s in states(root).values())


@pytest.mark.parametrize("by,claims", [(" ", []), ("確認者", ["CLAIM-0001=ok"]), ("確認者", ["CLAIM-0001"])])
def test_invalid_input_is_rejected(root, by, claims):
    assert record(root, by, all_ok=True, claim_args=claims)["result"] == "fail"
    assert not (root / review.RECEIPTS).exists()


def test_machine_check_failure_writes_nothing(root):
    path = root / review.PROV
    prov = yamlio.load(path)
    prov["records"][1]["excerpt_sha256"] = "0" * 64
    path.write_text(yamlio.dumps(prov), encoding="utf-8", newline="\n")
    before = path.read_bytes()
    r = record(root, "確認者", all_ok=True)
    assert r["result"] == "fail" and r["receipts_added"] == []
    assert path.read_bytes() == before and not (root / review.RECEIPTS).exists()


def review_files(root):
    return {rel: (root / rel).read_bytes() if (root / rel).exists() else None
            for rel in (review.PROV, review.RECEIPTS, review.SAMPLES)}


def test_individual_sample_issue_leaves_other_items_unreviewed(root):
    r = record(root, "確認者", issues=["EXTRACT-FX-001:table:列の順序が乱れる"])
    assert r["result"] == "pass" and r["sample_items"] == 1
    items = yamlio.load(root / review.SAMPLES)["reviews"][0]["items"]
    assert [(i["result"], i["note"]) for i in items] == [(None, None), ("issue", "列の順序が乱れる")]
    assert all(s["support_status"] == "unreviewed" for s in states(root).values())
    # 後続の一括判定は、既に記録した問題を上書きしない。
    assert record(root, "確認者", all_ok=True)["sample_items"] == 1
    items = yamlio.load(root / review.SAMPLES)["reviews"][0]["items"]
    assert [i["result"] for i in items] == ["ok", "issue"]


@pytest.mark.parametrize("judgements", [
    {"rejects": ["EV-9999"]},
    {"claim_args": ["CLAIM-9999=not_supported"]},
    {"claim_args": ["CLAIM-0001=supported", "CLAIM-0001=not_supported"]},
    {"issues": ["EXTRACT-FX-999:table:問題"]},
    {"issues": ["EXTRACT-FX-001:typo:問題"]},
    {"issues": ["EXTRACT-FX-001:table"]},
    {"issues": ["EXTRACT-FX-001:table: "]},
    {"issues": ["EXTRACT-FX-001:table:問題", "EXTRACT-FX-001:table:別の問題"]},
])
def test_invalid_exception_with_all_ok_writes_nothing(root, judgements):
    before = review_files(root)
    r = record(root, "確認者", all_ok=True, **judgements)
    assert r["result"] == "fail" and r["errors"]
    assert r["receipts_added"] == [] and r["sample_items"] == 0
    assert review_files(root) == before


@pytest.mark.parametrize("status", ["not_supported", "unclear"])
def test_explicit_claim_corrects_reviewed_judgement_and_keeps_history(root, status):
    assert record(root, "初回確認者", all_ok=True)["result"] == "pass"
    original = yamlio.load(root / review.RECEIPTS)["receipts"]
    r = record(root, "訂正者", claim_args=[f"CLAIM-0001={status}"])
    assert r["result"] == "pass" and r["receipts_added"] == ["VR-0003"]
    assert states(root)[("EV-0001", "CLAIM-0001")]["support_status"] == status
    prov = yamlio.load(root / review.PROV)
    assert prov["records"][0]["support_reviews"][0]["reviewed_by"] == "訂正者"
    assert prov["records"][0]["support_reviews"][1]["reviewed_by"] == "初回確認者"
    receipts = yamlio.load(root / review.RECEIPTS)["receipts"]
    assert receipts[:2] == original
    # 不支持・不明の判定から supported への明示的な訂正も受け付ける。
    assert record(root, "再確認者", claim_args=["CLAIM-0001=supported"])["receipts_added"] == ["VR-0004"]
    assert states(root)[("EV-0001", "CLAIM-0001")]["support_status"] == "supported"


def test_reject_overrides_old_confirmation_without_deleting_history(root):
    assert record(root, "確認者", all_ok=True)["result"] == "pass"
    original = yamlio.load(root / review.RECEIPTS)["receipts"]
    r = record(root, "訂正者", rejects=["EV-0002"])
    assert r["result"] == "pass" and r["receipts_added"] == ["VR-0003"]
    assert states(root)[("EV-0002", "CLAIM-0003")]["source_verification"] == "unverified"
    assert states(root)[("EV-0001", "CLAIM-0001")]["source_verification"] == "verified"
    assert yamlio.load(root / review.RECEIPTS)["receipts"][:2] == original
    assert record(root, "再確認者", all_ok=True)["receipts_added"] == ["VR-0004"]
    assert states(root)[("EV-0002", "CLAIM-0003")]["source_verification"] == "verified"


def test_explicit_sample_issue_corrects_completed_item(root):
    assert record(root, "確認者", all_ok=True)["result"] == "pass"
    r = record(root, "訂正者", issues=["EXTRACT-FX-001:table:列:順序が乱れる"])
    assert r["result"] == "pass" and r["sample_items"] == 1 and r["receipts_added"] == []
    rv = yamlio.load(root / review.SAMPLES)["reviews"][0]
    assert [(i["result"], i["note"]) for i in rv["items"]] == [("ok", None), ("issue", "列:順序が乱れる")]
    assert rv["checked_by"] == "訂正者"


def test_new_body_reusing_reviewed_claim_is_shown_and_can_be_adopted(root):
    assert record(root, "確認者", all_ok=True)["result"] == "pass"
    index = yamlio.load(root / review.INDEX)
    index["entries"].append({"knowledge_id": "KN-002", "kind": "method", "title": "追加の方法",
                             "path": "library/methods/b.md", "claims": ["CLAIM-0001"], "terms": [], "conflicts": []})
    (root / review.INDEX).write_text(yamlio.dumps(index), encoding="utf-8")
    (root / "library/methods/b.md").write_text("# 追加の方法\n\n[CLAIM-0001]\n", encoding="utf-8")

    def adoption():
        prov, receipts, _ = review._load(root)
        derived = provenance.derive(prov, receipts, root, root / "extracted")
        return library.adoption({"provenance": prov, "index": index}, receipts, derived)

    assert adoption() == {"KN-001": "adopted", "KN-002": "hypothesis"}
    assert review.sheet(root, root / "extracted")["evidence"] == 1
    assert "library/methods/b.md" in (root / review.SHEET).read_text(encoding="utf-8")
    r = record(root, "確認者", all_ok=True)
    assert r["result"] == "pass" and r["receipts_added"] == ["VR-0003"]
    assert adoption() == {"KN-001": "adopted", "KN-002": "adopted"}
    assert review.sheet(root, root / "extracted")["evidence"] == 0


def test_claim_hash_mismatch_writes_nothing(root):
    assert record(root, "確認者", all_ok=True)["result"] == "pass"
    prov = yamlio.load(root / review.PROV)
    prov["records"][0]["support_reviews"][0]["claim"] += "（変更）"
    (root / review.PROV).write_text(yamlio.dumps(prov), encoding="utf-8")
    before = review_files(root)
    r = record(root, "確認者", all_ok=True)
    assert r["result"] == "fail" and any("CLAIM-0001" in e for e in r["errors"])
    assert r["receipts_added"] == [] and r["sample_items"] == 0
    assert review_files(root) == before
