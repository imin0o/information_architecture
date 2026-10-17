"""コンテンツ・概念・ノードの参照整合性、循環・到達性、網羅性（AC-03）。"""

import copy

import pytest

from iakit import yamlio


def concept(d, cid):
    return next(c for c in d["controlled-vocabulary.yaml"]["concepts"] if c["concept_id"] == cid)


def node(d, nid):
    return next(n for n in d["sitemap.yaml"]["nodes"] if n["node_id"] == nid)


def item(d, cid):
    return next(i for i in d["content-inventory.yaml"]["items"] if i["content_id"] == cid)


def has(errors, text):
    return any(text in e for e in errors)


def test_homonym_requires_scope_note(check_bundle):
    assert has(check_bundle(lambda d: concept(d, "CONCEPT-009").update(scope_note=None)), "同名異義語「サポート」")


def test_homonym_with_scope_notes_is_allowed(bundle_docs, check_bundle):
    labels = [c for c in bundle_docs["controlled-vocabulary.yaml"]["concepts"] if "サポート" in c["alt_labels"]]
    assert len(labels) == 2
    assert check_bundle() == []


def test_label_is_not_identifier_duplicate_id_rejected(check_bundle):
    assert has(check_bundle(lambda d: concept(d, "CONCEPT-010").update(concept_id="CONCEPT-009")), "重複")


@pytest.mark.parametrize("broader", [["CONCEPT-002"], ["CONCEPT-005", "CONCEPT-003"]], ids=["direct", "poly"])
def test_broader_cycle_is_rejected(check_bundle, broader):
    assert has(check_bundle(lambda d: concept(d, "CONCEPT-001").update(broader=broader)), "循環")


def test_mutual_related_is_allowed(check_bundle):
    # 循環を禁止するのは上位・下位関係だけ。関連関係の相互参照は許容する
    assert check_bundle(lambda d: concept(d, "CONCEPT-001").update(related=["CONCEPT-010"])) == []


def test_unknown_concept_reference_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: concept(d, "CONCEPT-002").update(related=["CONCEPT-099"])), "CONCEPT-099")


def test_placed_content_must_be_classified(check_bundle):
    errors = check_bundle(lambda d: d["taxonomy.yaml"]["assignments"].pop(0))
    assert has(errors, "CONTENT-001: 配置済みなのに分類されていない")


def test_excluded_content_must_not_be_classified_or_placed(check_bundle):
    errors = check_bundle(lambda d: item(d, "CONTENT-014").update(status="excluded", reason="問い合わせは別サイト"))
    assert has(errors, "excluded のコンテンツを分類") and has(errors, "excluded の CONTENT-014 を配置")


def test_unprocessed_and_excluded_need_reason(bundle_docs):
    d = copy.deepcopy(bundle_docs["content-inventory.yaml"])
    d["items"][12]["reason"] = None
    assert yamlio.schema_errors(d, "content-inventory.schema.json")


def test_concept_outside_schemes_is_rejected(check_bundle):
    def orphan(d):
        d["taxonomy.yaml"]["schemes"].pop(1)
    assert has(check_bundle(orphan), "どの体系の最上位概念からもたどれない")


def test_reference_to_old_vocabulary_version_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: d["taxonomy.yaml"]["vocabulary"].update(version=2)), "現行の成果物と一致しない")


def test_unreachable_node_is_rejected(check_bundle):
    def detach(d):
        node(d, "NODE-011")["parent"] = "NODE-012"
        d["sitemap.yaml"]["nodes"].append({"node_id": "NODE-012", "label": "孤立", "parent": "NODE-011",
                                           "concept_id": None, "content_ids": [], "links": []})
    errors = check_bundle(detach)
    assert has(errors, "循環") and has(errors, "ルートから到達できない")


def test_second_root_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: node(d, "NODE-011").update(parent=None)), "ルート以外に親のないノード")


def test_placed_content_must_appear_in_sitemap(check_bundle):
    errors = check_bundle(lambda d: node(d, "NODE-011").update(content_ids=[]))
    assert has(errors, "CONTENT-014: 配置済みなのにどのノードにも置かれていない")


def test_task_path_must_follow_edges(check_bundle):
    def jump(d):
        d["sitemap.yaml"]["tasks"][0]["path"] = ["NODE-001", "NODE-009"]
    assert has(check_bundle(jump), "NODE-001 から NODE-009 への経路がない")


def test_scheme_must_reference_current_decision(check_bundle):
    errors = check_bundle(lambda d: d["taxonomy.yaml"]["schemes"][0].update(
        decision_ref={"decision_id": "IA-001", "decision_revision": 1}))
    assert has(errors, "現行でない判断 IA-001")
