"""知識ライブラリの検査（BR-02・BR-04）。自作PDFの出典登録簿と照合記録だけを使う。"""

import shutil

import pytest

from iakit import library, manifest, yamlio
from iakit.paths import FIXTURES, MANIFEST, ROOT

RUN = FIXTURES / "contracts/webia-run"
ADOPTED = "# 前付けの番号\n\n前付けはローマ数字で数える [CLAIM-0001]。\n"
PENDING = "# 本文の番号\n\n本文は1から数える [CLAIM-0003]。\n"


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_text(yamlio.dumps(data), encoding="utf-8", newline="\n")


@pytest.fixture
def lib(tmp_path):
    shutil.copytree(ROOT / "tests/cases", tmp_path / "tests/cases")
    docs = {
        "provenance": yamlio.load(RUN / "provenance.yaml"),
        "receipts": yamlio.load(RUN / "receipts.yaml"),
        "index": {"schema_version": "1.0", "entries": [
            {"knowledge_id": "KN-001", "kind": "method", "title": "前付け", "path": "library/methods/a.md",
             "claims": ["CLAIM-0001"], "terms": ["TERM-0001"], "conflicts": []},
            {"knowledge_id": "KN-002", "kind": "method", "title": "本文", "path": "library/methods/b.md",
             "claims": ["CLAIM-0003"], "terms": [], "conflicts": []}],
            "needs": [{"need_id": "NEED-01", "cases": ["CASE-TAXONOMY-001"], "rubric_items": ["R-02"],
                       "description": "例", "entries": ["KN-001"], "gap": None},
                      {"need_id": "NEED-02", "cases": ["CASE-WEBIA-001"], "rubric_items": ["R-05"],
                       "description": "例", "entries": [], "gap": "未統合"}]},
        "terms": {"schema_version": "1.0", "terms": [
            {"term_id": "TERM-0001", "en": "front matter", "ja": "前付け", "alt_ja": [], "definition": "例",
             "claims": ["CLAIM-0001"]}]},
        "conflicts": {"schema_version": "1.0", "conflicts": []},
        "quotes": {"schema_version": "1.0", "quotes": []},
        "generations": {"schema_version": "1.0", "generations": []},
        "samples": {"schema_version": "1.0", "reviews": []},
        "coverage": {"schema_version": "1.0", "books": [
            {"book_id": b["book_id"], "status": "not_started" if b["original"]["received"] else "not_received",
             "remaining": "全体未統合", "mvp_impact": "例"} for b in yamlio.load(MANIFEST)["books"]]},
    }
    files = {"methods/a.md": ADOPTED, "methods/b.md": PENDING}
    bind = {"library/methods/a.md": True}

    def run(mutate=None):
        if mutate:
            mutate(docs, files)
        for rel, text in files.items():
            write(tmp_path, f"library/{rel}", text)
        # 照合記録の対象本文に、人がレビューした本文として a.md を入れる
        for rc in docs["receipts"]["receipts"]:
            rc["targets"]["knowledge_files"] = [{"path": p, "sha256": manifest.sha256_file(tmp_path / p)}
                                                for p, on in bind.items() if on]
        for key, (rel, _) in library.FILES.items():
            write(tmp_path, rel, docs[key])
        write(tmp_path, library.RECEIPTS[0], docs["receipts"])
        return library.check(tmp_path, tmp_path / "extracted")
    run.bind = bind
    return run


def test_valid_library_derives_adoption_from_reviews(lib):
    r = lib()
    assert r["result"] == "pass", r["errors"]
    assert r["knowledge"] == {"adopted": ["KN-001"], "hypothesis": ["KN-002"], "not_supported": []}
    assert r["needs"] == {"NEED-01": "adopted", "NEED-02": "gap"}
    assert r["quote_ledger"]["FIXTURE-page-labels"]["previews"] == 2
    assert r["coverage"]["extractions"]["EXTRACT-FX-001"]["integrated"] == [[4, 5], [10, 12]]
    assert r["human_review"] == "not_run"


def test_body_not_in_receipt_is_not_adopted(lib):
    lib.bind["library/methods/a.md"] = False
    assert lib()["knowledge"]["adopted"] == []


def test_rule_added_after_review_invalidates_adoption(lib, tmp_path):
    """引用のない規則を追記しても索引との一致は保たれるが、レビュー後の変更として照合記録が無効になる。"""
    assert lib()["knowledge"]["adopted"] == ["KN-001"]
    write(tmp_path, "library/methods/a.md", ADOPTED + "未確認の規則を追記する。\n")
    r = library.check(tmp_path, tmp_path / "extracted")
    assert r["result"] == "pass" and r["knowledge"]["adopted"] == []


def test_not_supported_claim_is_reported_separately(lib):
    def mutate(d, f):
        d["index"]["entries"][0]["claims"] = ["CLAIM-0002"]
        d["terms"]["terms"][0]["claims"] = ["CLAIM-0002"]
        f["methods/a.md"] = "# 例\n\n無関係な主張 [CLAIM-0002]。\n"
    assert lib(mutate)["knowledge"]["not_supported"] == ["KN-001"]


def test_hypothesis_section_is_excluded_and_must_be_last():
    text = "# 方法\n\n手順 [CLAIM-0001]。\n\n## 仮説・補完\n\n推測。\n"
    assert "推測" not in library.adoptable_text(text) and "手順" in library.adoptable_text(text)
    assert library._boundary_errors("x.md", text) == []
    assert library._boundary_errors("x.md", text + "\n## 例外\n\n後ろの節。\n")


def test_coverage_must_match_manifest(lib):
    def mutate(d, f):
        d["coverage"]["books"][0]["status"] = "not_received"
        d["coverage"]["books"].pop()
    errors = lib(mutate)["errors"]
    assert any("受領状態が一致しない" in e for e in errors)
    assert any("登録簿の BOOK-" in e for e in errors)


def test_body_citations_must_match_index(lib):
    r = lib(lambda d, f: f.update({"methods/a.md": ADOPTED + "追加 [CLAIM-0003]。\n"}))
    assert any("本文が引く主張" in e for e in r["errors"])


def test_unregistered_body_is_rejected(lib):
    r = lib(lambda d, f: f.update({"concepts/orphan.md": "# 未登録\n"}))
    assert any("索引に登録されていない" in e for e in r["errors"])


@pytest.mark.parametrize("key,where,value", [
    ("terms", ("terms", 0, "claims"), ["CLAIM-0999"]),
    ("index", ("entries", 0, "terms"), ["TERM-0999"]),
    ("index", ("needs", 0, "cases"), ["CASE-WEBIA-999"]),
    ("index", ("needs", 0, "rubric_items"), ["R-99"]),
])
def test_unknown_references_are_rejected(lib, key, where, value):
    def mutate(d, f):
        d[key][where[0]][where[1]][where[2]] = value
    assert any("未登録の" in e for e in lib(mutate)["errors"])


def test_need_without_entries_must_state_gap(lib):
    def mutate(d, f):
        d["index"]["needs"][1]["gap"] = None
    assert any("gap が null" in e for e in lib(mutate)["errors"])


def test_conflict_needs_two_claims(lib):
    def mutate(d, f):
        d["conflicts"]["conflicts"] = [{"conflict_id": "CONFLICT-001", "topic": "例", "resolution": None,
                                        "positions": [{"claim_id": "CLAIM-0001", "applicability": "a"},
                                                      {"claim_id": "CLAIM-0001", "applicability": "b"}]}]
    assert any("2件未満" in e for e in lib(mutate)["errors"])


def test_long_direct_quote_is_rejected(lib):
    def mutate(d, f):
        d["quotes"]["quotes"] = [{"quote_id": "QUOTE-0001", "source_id": "FIXTURE-page-labels",
                                  "extracted_ref": {"extraction_id": "EXTRACT-FX-001", "start_line": 4, "end_line": 5},
                                  "use": "direct_quote", "text": " ".join(["word"] * 51),
                                  "used_in": "library/methods/a.md", "bundled_in": [], "visibility": "private"}]
    assert any("50語を超える" in e for e in lib(mutate)["errors"])


def test_self_declared_adoption_is_not_accepted(lib):
    """照合記録のない主張を supported と書いても adopted にならない。"""
    def mutate(d, f):
        d["receipts"]["receipts"] = []
    assert lib(mutate)["knowledge"]["adopted"] == []


def test_generation_records_instruction_hash_and_edits(lib, tmp_path):
    write(tmp_path, "library/build-logs/instructions/v1.md", "手順\n")
    sha = manifest.sha256_file(tmp_path / "library/build-logs/instructions/v1.md")

    def mutate(d, f):
        d["generations"]["generations"] = [{
            "generation_id": "GEN-001", "generated_at": "2026-10-17T18:00:00+09:00", "host": "test", "model": "m",
            "approval": "a", "instruction": {"path": "library/build-logs/instructions/v1.md", "sha256": sha},
            "inputs": [{"extraction_id": "EXTRACT-FX-001", "extracted_sha256": "0" * 64, "start_line": 1, "end_line": 2}],
            "searches": [{"extraction_id": "EXTRACT-FX-001", "extracted_sha256": "0" * 64, "pattern": "p",
                          "lines": [7, 9], "max_chars": 110, "detail": "complete", "recovered_from": "test"}],
            "outputs": [{"path": "library/methods/a.md", "sha256": "0" * 64}],
            "review": {"reviewed_by": None, "reviewed_at": None}}]
    r = lib(mutate)
    assert r["result"] == "pass", r["errors"]
    assert r["edited_after_generation"] == ["library/methods/a.md"]
    assert r["coverage"]["extractions"]["EXTRACT-FX-001"]["read"] == [[1, 2]]
    assert r["coverage"]["extractions"]["EXTRACT-FX-001"]["displayed_lines"] == 2
    write(tmp_path, "library/build-logs/instructions/v1.md", "変更した手順\n")
    assert any("指示のハッシュ" in e for e in lib()["errors"])
