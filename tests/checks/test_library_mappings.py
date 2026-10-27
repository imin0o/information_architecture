"""UI/UX原則カードとの知識対応表の検査。自作の主張とカードだけを使う。"""

import copy

import pytest

from iakit import library_mappings, provenance, yamlio
from iakit.paths import FIXTURES

PROV = yamlio.load(FIXTURES / "contracts/webia-run/provenance.yaml")
CLAIMS = provenance.claims_index(PROV)
ORIGINAL = "a" * 64
SOURCES = {"FIXTURE-page-labels": ORIGINAL}
INDEX = {"entries": [{"knowledge_id": "KN-001", "claims": ["CLAIM-0001", "CLAIM-0003"]},
                     {"knowledge_id": "KN-002", "claims": ["CLAIM-0002"]}]}
CARD = {"id": "UX-PRINCIPLE-0001", "recommendation": "前付けはローマ数字で数える。",
        "conditions": [{"text": "前付けがある本に限る", "basis": "source", "source_id": "fixture-page-labels"}],
        "evidence": [{"source_id": "fixture-page-labels", "locator": "p.1"}]}


def mapping(**over):
    m = {"claim_id": "CLAIM-0001", "knowledge_id": "KN-001", "principle_id": "UX-PRINCIPLE-0001",
         "ux_field": "conditions[0]", "relation": "broad", "scope_difference": "例",
         "ia_evidence_id": CLAIMS["CLAIM-0001"]["evidence_id"], "ux_locator": "p.1",
         "ia_claim_sha256": CLAIMS["CLAIM-0001"]["claim_sha256"],
         "ia_applicability_sha256": provenance.text_sha256(CLAIMS["CLAIM-0001"]["applicability"]),
         "ux_text_sha256": provenance.text_sha256(CARD["conditions"][0]["text"]),
         "ux_context_sha256": library_mappings.context_sha256(CARD),
         "reviewed_by": "tester", "reviewer_kind": "agent", "reviewed_at": "2026-10-27"}
    return m | over


DOC = {"schema_version": "1.0",
       "scope": {"ia_knowledge": ["KN-001"], "ux_principles": {"from": "UX-PRINCIPLE-0001", "to": "UX-PRINCIPLE-0009"}},
       "sources": [{"book": "自作", "edition": "1", "ia_source_id": "FIXTURE-page-labels",
                    "ux_source_id": "fixture-page-labels", "original_sha256": ORIGINAL, "sections": ["前付け"]}],
       "claims": [mapping()]}


@pytest.fixture
def ux(tmp_path, monkeypatch):
    """IAリポジトリと並ぶ ui_ux を作り、環境変数の指定は外す。"""
    monkeypatch.delenv(library_mappings.UIUX_ENV, raising=False)
    root, ux_root = tmp_path / "information_architecture", tmp_path / "ui_ux"
    root.mkdir()
    (ux_root / "knowledge/principles").mkdir(parents=True)
    (ux_root / "sources").mkdir()

    def write(card=CARD, local_sha=ORIGINAL):
        text = f"---\n{yamlio.dumps(card)}---\n\n## 補足\n"
        (ux_root / f"knowledge/principles/{card['id']}.md").write_text(text, encoding="utf-8", newline="\n")
        (ux_root / "sources/local-index.yaml").write_text(
            yamlio.dumps({"fixture-page-labels": [{"file": "x.pdf", "sha256": local_sha}]}), encoding="utf-8")
        return root
    write.ux_root = ux_root
    return write


def run(root, doc=DOC, sources=SOURCES):
    assert yamlio.schema_errors(doc, library_mappings.FILE[1]) == []
    return library_mappings.check(doc, INDEX, PROV, sources, root)


def with_claim(**over):
    doc = copy.deepcopy(DOC)
    doc["claims"] = [mapping(**over)]
    return doc


def test_valid_mapping_checks_both_sides(ux):
    errors, report = run(ux())
    assert errors == []
    assert report == {"claims": 1, "relations": {"broad": 1}, "unmapped_claims": ["CLAIM-0003"], "ux_check": "checked"}


def test_ux_side_is_not_run_without_repository(tmp_path, monkeypatch):
    monkeypatch.delenv(library_mappings.UIUX_ENV, raising=False)
    errors, report = run(tmp_path / "information_architecture")
    assert errors == [] and report["ux_check"] == "not_run"


def test_explicit_ux_root_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv(library_mappings.UIUX_ENV, str(tmp_path / "missing"))
    errors, report = run(tmp_path / "information_architecture")
    assert report["ux_check"] == "not_run"
    assert any("knowledge/principles がない" in e for e in errors)


def test_env_points_to_ux_repository(ux, monkeypatch, tmp_path):
    ux()
    monkeypatch.setenv(library_mappings.UIUX_ENV, str(ux.ux_root))
    errors, report = run(tmp_path / "elsewhere")
    assert errors == [] and report["ux_check"] == "checked"


@pytest.mark.parametrize("over, message", [
    ({"claim_id": "CLAIM-0099"}, "未登録の主張 CLAIM-0099"),
    ({"claim_id": "CLAIM-0002"}, "CLAIM-0002 は KN-001 が引く主張ではない"),
    ({"knowledge_id": "KN-002", "claim_id": "CLAIM-0002"}, "KN-002 は対応表の範囲外"),
    ({"principle_id": "UX-PRINCIPLE-0010"}, "UX-PRINCIPLE-0010 は対応表の範囲外"),
    ({"ia_evidence_id": "EV-0009"}, "ia_evidence_id が主張の根拠"),
    ({"ia_claim_sha256": "b" * 64}, "主張の版が変わった"),
])
def test_ia_side_errors(ux, over, message):
    errors, _ = run(ux(), with_claim(**over))
    assert any(message in e for e in errors), errors


def test_unregistered_knowledge_in_scope(ux):
    doc = copy.deepcopy(DOC)
    doc["scope"]["ia_knowledge"] = ["KN-001", "KN-404"]
    assert "scope: 未登録の知識 KN-404" in run(ux(), doc)[0]


def test_duplicate_pair_is_rejected(ux):
    doc = copy.deepcopy(DOC)
    doc["claims"].append(mapping(relation="related"))
    assert any("対応が重複している" in e for e in run(ux(), doc)[0])


def test_claim_from_unpaired_source_is_rejected(ux):
    doc = copy.deepcopy(DOC)
    doc["sources"][0]["ia_source_id"] = "BOOK-001"
    errors, _ = run(ux(), doc, sources=SOURCES | {"BOOK-001": ORIGINAL})
    assert any("主張の出典 FIXTURE-page-labels が sources にない" in e for e in errors), errors


def test_original_hash_must_match_both_registries(ux):
    errors, _ = run(ux(local_sha="c" * 64), sources={"FIXTURE-page-labels": "d" * 64})
    assert "sources: FIXTURE-page-labels の原典ハッシュが出典登録簿と一致しない" in errors
    assert any("UI/UX側の原典ハッシュ" in e for e in errors)


@pytest.mark.parametrize("card, over, message", [
    (CARD | {"conditions": [{"text": "前付けがある本に限らない", "basis": "source"}]}, {}, "conditions[0] が変わった"),
    (CARD, {"ux_field": "exceptions[0]"}, "原則カードに exceptions[0] がない"),
    (CARD | {"id": "UX-PRINCIPLE-0002"}, {}, "原則カード UX-PRINCIPLE-0001 がない"),
    (CARD | {"evidence": [{"source_id": "other-book", "locator": "p.1"}]}, {}, "主張と対応する出典 fixture-page-labels がない"),
])
def test_ux_side_errors(ux, card, over, message):
    errors, _ = run(ux(card), with_claim(**over))
    assert any(message in e for e in errors), errors


@pytest.mark.parametrize("changes", [
    {"recommendation": "This recommendation has changed."},
    {"exceptions": [{"text": "This rule no longer has an exception.", "basis": "interpretation"}]},
    {"tradeoffs": [{"text": "A newly identified cost.", "basis": "interpretation"}]},
    {"conditions": CARD["conditions"] + [{"text": "An additional restriction.", "basis": "interpretation"}]},
    {"evidence": [{"source_id": "fixture-page-labels", "locator": "p.9"}]},
])
def test_context_revision_requires_mapping_review(ux, changes):
    errors, _ = run(ux(CARD | changes))
    assert any("文脈が変わった" in e for e in errors), errors


@pytest.mark.parametrize("basis, source_id", [("source", "other-book"), ("interpretation", None), ("source", None)])
def test_selected_field_must_come_from_paired_source(ux, basis, source_id):
    card = copy.deepcopy(CARD)
    card["conditions"][0]["basis"] = basis
    card["conditions"][0].pop("source_id", None)
    if source_id is not None:
        card["conditions"][0]["source_id"] = source_id
    card["evidence"].append({"source_id": "other-book", "locator": "p.9"})
    errors, _ = run(ux(card), with_claim(ux_context_sha256=library_mappings.context_sha256(card)))
    assert any("該当欄の出典" in e for e in errors), errors


def test_recommendation_uses_card_evidence(ux):
    errors, _ = run(ux(), with_claim(ux_field="recommendation",
                                   ux_text_sha256=provenance.text_sha256(CARD["recommendation"])))
    assert errors == []


def test_review_metadata_does_not_invalidate_mapping(ux):
    errors, _ = run(ux(CARD | {"title": "Updated display title", "reviewed_by": "another reviewer"}))
    assert errors == []


def test_context_hash_is_required_even_without_ux_repository():
    doc = copy.deepcopy(DOC)
    del doc["claims"][0]["ux_context_sha256"]
    assert yamlio.schema_errors(doc, library_mappings.FILE[1])


@pytest.mark.parametrize("card", [
    ["not-an-object"], "not-an-object", 1,
    CARD | {"recommendation": ["not-text"]},
    CARD | {"conditions": "not-an-array"},
    CARD | {"conditions": [None]},
    CARD | {"conditions": [{"text": ["not-text"], "basis": "source"}]},
    CARD | {"evidence": [None]},
])
def test_malformed_card_is_a_reported_error(ux, card):
    root = ux()
    (ux.ux_root / "knowledge/principles/UX-PRINCIPLE-0001.md").write_text(
        f"---\n{yamlio.dumps(card)}---\n", encoding="utf-8")
    errors, report = run(root)
    assert any("原則カードを読めない" in e for e in errors), errors
    assert report["ux_check"] == "checked"


def review(**over):
    """生成者と別の提供元による照合記録。比較した版は mapping() の現在の版と同じにする。"""
    m = mapping()
    r = {"generator": {"provider": "anthropic", "session": "gen-1"},
         "reviewer": {"provider": "openai", "session": "rev-1"},
         "ia_claim_sha256": m["ia_claim_sha256"], "ux_text_sha256": m["ux_text_sha256"],
         "ia_applicability_sha256": m["ia_applicability_sha256"],
         "ux_context_sha256": m["ux_context_sha256"],
         "conditions_comparison": "前付けの有無という条件が両側で一致する", "verdict": "exact"}
    return r | over


def independent(**over):
    return with_claim(relation="exact", reviewer_kind="independent_agent", independent_review=review(**over))


def test_exact_requires_human_or_independent_review():
    schema = library_mappings.FILE[1]
    assert yamlio.schema_errors(with_claim(relation="exact"), schema)
    assert yamlio.schema_errors(with_claim(relation="exact", reviewer_kind="human"), schema) == []
    assert yamlio.schema_errors(with_claim(relation="exact", reviewer_kind="independent_agent"), schema)
    assert yamlio.schema_errors(independent(), schema) == []


def test_independent_review_is_accepted(ux):
    errors, report = run(ux(), independent())
    assert errors == [] and report["relations"] == {"exact": 1}


@pytest.mark.parametrize("reviewer_kind", ["agent", "human", "independent_agent"])
@pytest.mark.parametrize("ux_available", [True, False])
def test_ia_applicability_revision_requires_mapping_review(ux, tmp_path, reviewer_kind, ux_available):
    root = ux() if ux_available else tmp_path / "isolated/information_architecture"
    doc = independent() if reviewer_kind == "independent_agent" else with_claim(
        reviewer_kind=reviewer_kind, relation="exact" if reviewer_kind == "human" else "close")
    assert run(root, doc)[0] == []
    prov = copy.deepcopy(PROV)
    prov["records"][0]["support_reviews"][0]["applicability"] = "前付けと本文を同じ番号体系で数える文書だけ"
    errors, report = library_mappings.check(doc, INDEX, prov, SOURCES, root)
    assert any("適用条件の版が変わった" in e for e in errors), errors
    assert report["ux_check"] == ("checked" if ux_available else "not_run")


def test_mapping_refresh_does_not_refresh_independent_applicability_review(ux):
    root, doc, prov = ux(), independent(), copy.deepcopy(PROV)
    claim = prov["records"][0]["support_reviews"][0]
    claim["applicability"] = "前付けと本文を同じ番号体系で数える文書だけ"
    current = provenance.text_sha256(claim["applicability"])
    doc["claims"][0]["ia_applicability_sha256"] = current
    errors, _ = library_mappings.check(doc, INDEX, prov, SOURCES, root)
    assert any("照合した後に対象が変わった" in e for e in errors), errors
    doc["claims"][0]["independent_review"]["ia_applicability_sha256"] = current
    assert library_mappings.check(doc, INDEX, prov, SOURCES, root)[0] == []


@pytest.mark.parametrize("in_review", [False, True])
def test_ia_applicability_hash_is_required(in_review):
    doc = independent()
    target = doc["claims"][0]["independent_review"] if in_review else doc["claims"][0]
    del target["ia_applicability_sha256"]
    assert any("required: ia_applicability_sha256" in e for e in yamlio.schema_errors(doc, library_mappings.FILE[1]))


def test_ia_review_metadata_does_not_invalidate_mapping(ux):
    prov = copy.deepcopy(PROV)
    prov["records"][0]["support_reviews"][0].update(reviewed_by="another reviewer", reviewed_at="2026-10-27T12:00:00+09:00")
    assert library_mappings.check(independent(), INDEX, prov, SOURCES, ux())[0] == []


@pytest.mark.parametrize("over, message", [
    ({"reviewer": {"provider": "anthropic", "session": "rev-1"}}, "自己確認"),
    ({"ux_context_sha256": "b" * 64}, "照合した後に対象が変わった"),
    ({"ia_claim_sha256": "b" * 64}, "照合した後に対象が変わった"),
    ({"verdict": "close"}, "照合の判定 close が relation exact と一致しない"),
])
def test_independent_review_errors(ux, over, message):
    errors, _ = run(ux(), independent(**over))
    assert any(message in e for e in errors), errors


def test_direction_is_fixed_by_schema():
    reversed_ids = with_claim(claim_id="UX-PRINCIPLE-0001", principle_id="CLAIM-0001")
    assert yamlio.schema_errors(reversed_ids, library_mappings.FILE[1])


def test_repository_mapping_passes_ia_side():
    from iakit import library
    from iakit.paths import ROOT
    doc = yamlio.load(ROOT / library_mappings.FILE[0])
    assert yamlio.schema_errors(doc, library_mappings.FILE[1]) == []
    errors = library_mappings.ia_errors(doc, yamlio.load(ROOT / library.FILES["index"][0]),
                                        yamlio.load(ROOT / library.FILES["provenance"][0]), provenance.registered_sources())
    assert errors == []
