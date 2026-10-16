"""n-gram部分一致検出の評価（M1）。自作テキストだけを使う。"""

import pytest

from iakit import ngram

SOURCE = (
    "Synonyms map several labels to one concept. A label is not an identifier, and two concepts\n"
    "may share the label bank. Faceted classification suits collections that users filter by several\n"
    "independent attributes, such as material, period and place of origin. Hierarchies work when each\n"
    "item has one obvious parent. 情報設計では、ラベルと概念を区別し、同名異義語に別の識別子を与える。\n"
)
EXCERPT = "Faceted classification suits collections that users filter by several independent attributes"

CASES = [
    # (名前, 対象文書, 期待する検出)
    ("exact sentence", "Notes. " + EXCERPT + ", as we saw.", True),
    ("reflowed and recased", "FACETED classification suits\ncollections that users filter BY several independent", True),
    ("ligature and fullwidth", "Faceted classiﬁcation suits collections that users ﬁlter by several", True),
    ("hyphen at line end", "Faceted classi-\nfication suits collections that users filter by several", True),
    ("japanese copy", "メモ：情報設計では、ラベルと概念を区別し、同名異義語に別の識別子を与える。", True),
    ("paraphrase", "Use facets when people narrow a collection by independent properties like period.", False),
    ("common phrase", "This is one of the several independent attributes we use.", False),
]


@pytest.mark.parametrize(("name", "text", "expected"), CASES)
def test_detection_with_loose_normalization(name, text, expected):
    assert ngram.detected(ngram.match(text, SOURCE)) is expected, name


def test_strict_matching_misses_reflow_and_ligatures():
    # ハッシュ照合と同じ厳密な比較では表記差を検出できない。検出側では緩めた正規化が必要になる
    misses = [name for name, text, expected in CASES
              if expected and not ngram.detected(ngram.match(text, SOURCE, loose=False))]
    assert {"reflowed and recased", "ligature and fullwidth", "hyphen at line end"} <= set(misses)


def test_registered_excerpt_is_allowed_but_longer_copy_is_not():
    assert not ngram.detected(ngram.match(EXCERPT, SOURCE, allowed=[EXCERPT]))
    longer = EXCERPT + ", such as material, period and place of origin. Hierarchies work when each item"
    assert ngram.detected(ngram.match(longer, SOURCE, allowed=[EXCERPT]))


def test_short_quotes_below_n_are_not_detected():
    assert not ngram.detected(ngram.match("Synonyms map several labels", SOURCE))
