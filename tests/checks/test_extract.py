"""自作素材による抽出・位置対応の契約（M1）。"""

import pytest

from iakit import fixture_epub, fixture_pdf, fixture_pdf_unicode, snapshot, yamlio
from iakit.paths import FIXTURES


def make(tmp_path, rel, source_id=None):
    source_id = source_id or "FIXTURE-" + rel.split("/")[-1].split(".")[0]
    d = snapshot.create(FIXTURES / rel, source_id, tmp_path / "extracted", "trial")
    lines = (d / "text.txt").read_text(encoding="utf-8").split("\n")[:-1]
    return d, lines, yamlio.load(d / "locations.yaml"), yamlio.load(d / "extraction.yaml")


def page_lines(lines, page):
    return lines[page["start_line"] - 1:page["end_line"]]


def test_pdf_pages_keep_order_and_printed_labels(tmp_path):
    _, lines, loc, meta = make(tmp_path, "pdf/page-labels.pdf")
    expected = fixture_pdf.expected()
    assert [p["pdf_page_index"] for p in loc["pages"]] == [1, 2, 3, 4, 5]
    assert [p["printed_page_label"] for p in loc["pages"]] == [p["printed_label"] for p in expected["pages"]]
    for page, exp in zip(loc["pages"], expected["pages"]):
        assert page_lines(lines, page)[0] == exp["first_line"]
    assert loc["printed_page_numbers"] == "from_page_labels"
    assert meta["extractor"]["llm_calls"] == 0 and meta["extractor"]["upstream"]["code_executed"] is False


def test_pdf_keeps_line_end_hyphen_as_written(tmp_path):
    _, lines, _, _ = make(tmp_path, "pdf/page-labels.pdf")
    assert lines[-2].endswith("classi-") and lines[-1] == "fication."


def test_pdf_without_page_labels_has_no_printed_numbers(tmp_path):
    _, _, loc, _ = make(tmp_path, "pdf/unicode-layout.pdf")
    assert loc["page_labels"] == "absent" and loc["printed_page_numbers"] is None
    assert {p["printed_page_label"] for p in loc["pages"]} == {None}


def test_unicode_ligature_columns_and_type3_are_kept_without_conversion(tmp_path):
    _, lines, loc, meta = make(tmp_path, "pdf/unicode-layout.pdf")
    for page, exp in zip(loc["pages"], fixture_pdf_unicode.expected()["pages"]):
        assert page_lines(lines, page) == exp["lines"]
    text = "\n".join(lines)
    assert "café" in text and "café" in text  # NFC/NFDへ変換しない
    assert "ﬁ" in text and "office" in text  # 合字グリフはToUnicodeのまま
    assert "⼈" in text and "Ａ" in text  # 康熙部首・全角を畳み込まない
    assert meta["quality"]["invisible_codepoints"] == 0


def test_epub_spine_hrefs_element_ids_and_unreadable(tmp_path):
    _, lines, loc, _ = make(tmp_path, "epub/reflowable.epub")
    expected = fixture_epub.expected()
    docs = {d["href"]: d for d in loc["documents"]}
    assert [d["href"] for d in loc["documents"]] == [s["href"] for s in expected["spine"]]
    assert [d["linear"] for d in loc["documents"]] == [s["linear"] for s in expected["spine"]]
    for spine in expected["spine"]:
        assert [e["id"] for e in docs[spine["href"]]["elements"]] == spine.get("element_ids", [])
    ch1 = {e["id"]: lines[e["line"] - 1] for e in docs["text/ch1.xhtml"]["elements"]}
    assert ch1["p-1-1"].startswith("A label is not an identifier")
    assert ch1["sec-1-1"] == "1.1 Synonyms"
    unreadable = docs["text/ch2.xhtml"]["unreadable"]
    assert unreadable == [{"element_id": "fig-2-1", "kind": "image", "alt_text": False}]
    no_id = expected["paragraph_without_id"]
    hits = [i + 1 for i, line in enumerate(lines) if line.startswith(no_id["starts_with"])]
    doc = docs[no_id["href"]]
    assert len(hits) == 1 and doc["start_line"] <= hits[0] <= doc["end_line"]
    assert loc["printed_page_numbers"] is None and expected["printed_page_numbers"] is None


def test_epub_table_cells_are_tab_separated(tmp_path):
    _, lines, _, _ = make(tmp_path, "epub/reflowable.epub")
    assert "Path\tSteps" in lines and "Browse\t3" in lines


@pytest.mark.parametrize("rel", ["pdf/page-labels.pdf", "epub/reflowable.epub", "pdf/unicode-layout.pdf"])
def test_location_table_covers_every_line(tmp_path, rel):
    _, lines, loc, _ = make(tmp_path, rel)
    units = loc.get("pages") or loc.get("documents")
    covered = [n for u in units if u["status"] == "text" for n in range(u["start_line"], u["end_line"] + 1)]
    assert covered == list(range(1, len(lines) + 1))
