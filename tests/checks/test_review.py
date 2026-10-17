"""人の判定の記録（iakit review）。自作PDFの固定テキストと契約素材の出典登録簿だけを使う。"""

import pytest

from iakit import extract_cmd, manifest, provenance, review, snapshot, yamlio
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
