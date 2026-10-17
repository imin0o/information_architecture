"""自作素材による抽出・位置対応の契約（M1）。"""

import zipfile

import pytest

from iakit import extract_cmd, extract_epub, fixture_epub, fixture_pdf, fixture_pdf_unicode, snapshot, snapshot_check, yamlio
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


def epub_variant(tmp_path, transform):
    path = tmp_path / "variant.epub"
    with zipfile.ZipFile(FIXTURES / "epub/reflowable.epub") as original, zipfile.ZipFile(path, "w") as output:
        for member in original.infolist():
            raw = original.read(member.filename)
            output.writestr(member, transform(raw) if member.filename.endswith(".xhtml") else raw)
    return path


@pytest.mark.parametrize(("encoding", "bom"), [
    ("utf-8", b"\xef\xbb\xbf"),
    ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
    ("utf-16-le", b""), ("utf-16-be", b""),
])
def test_epub_encodings_keep_text_and_locations(tmp_path, encoding, bom):
    def encode(raw):
        declared = "UTF-8" if encoding == "utf-8" else "UTF-16"
        return bom + raw.decode("utf-8").replace('encoding="UTF-8"', f'encoding="{declared}"').encode(encoding)

    source = epub_variant(tmp_path, encode)
    d = snapshot.create(source, "FIXTURE-variant", tmp_path / "encoded", "trial")
    expected = snapshot.create(FIXTURES / "epub/reflowable.epub", "FIXTURE-variant", tmp_path / "expected", "trial")
    assert (d / "text.txt").read_bytes() == (expected / "text.txt").read_bytes()
    assert yamlio.load(d / "locations.yaml") == yamlio.load(expected / "locations.yaml")
    assert snapshot_check.verify(d, source) == []
    assert yamlio.load(d / "extraction.yaml")["quality"]["warning_count"] == 0


@pytest.mark.parametrize("transform", [
    lambda raw: b"\xff" + raw,
    lambda raw: raw.replace(b"<body>", b"<body>\x00"),
    lambda raw: raw.decode("utf-8").encode("utf-32"),
    lambda raw: raw.replace(b"UTF-8", b"ISO-8859-1"),
])
def test_epub_invalid_or_unsupported_encoding_is_rejected(tmp_path, transform):
    source = epub_variant(tmp_path, transform)
    root = tmp_path / "extracted"
    with pytest.raises(ValueError, match="XHTML"):
        snapshot.create(source, "FIXTURE-variant", root, "trial")
    assert not root.exists()


def test_idless_paragraph_uses_containing_section_instead_of_previous_paragraph(tmp_path):
    d, lines, _, _ = make(tmp_path, "epub/reflowable.epub")
    line = next(i + 1 for i, text in enumerate(lines) if text.startswith("This paragraph has no element ID"))
    assert extract_cmd.ref(d, str(line))["locator_candidate"] == {
        "epub_href": "text/ch1.xhtml", "element_id_candidate": "sec-1-1",
    }


@pytest.mark.parametrize(("body", "start", "end", "candidate"), [
    ('<p id="empty"></p><p id="actual">Alpha</p><p>Beta</p>', 1, 1, "actual"),
    ('<p id="empty"></p><p id="actual">Alpha</p><p>Beta</p>', 2, 2, None),
    ('<p id="first">Alpha</p><p id="second">Beta</p>', 1, 2, None),
    ('<p id="outer"><span id="inner">Alpha</span> Beta</p>', 1, 1, "outer"),
    ('<p>Before <span id="partial">Alpha</span> After</p>', 1, 1, None),
    ('<div id="outer"><p id="inner">Alpha</p></div>', 1, 1, "inner"),
    ('<p>Before <span id="middle">A\r\nB\r\nC</span> After</p>', 2, 2, "middle"),
    ('<p><span id="first">Alpha\n</span>Beta</p>', 1, 1, "first"),
])
def test_epub_candidate_must_contain_every_selected_line(body, start, end, candidate):
    parser = extract_epub.TextExtractor()
    parser.feed(body)
    parser.close()
    _, documents, _ = snapshot.assemble({"units": [{
        "href": "chapter.xhtml", "text": parser.text(), "anchors": parser.anchors, "error": None,
    }]})
    assert snapshot_check.locate({"format": "epub", "documents": documents}, start, end) == {
        "epub_href": "chapter.xhtml", "element_id_candidate": candidate,
    }
    assert all(e["id"] != "empty" for e in documents[0]["elements"])


def test_legacy_epub_elements_without_end_line_only_return_href(tmp_path):
    _, _, loc, _ = make(tmp_path, "epub/reflowable.epub")
    doc = loc["documents"][1]
    for element in doc["elements"]:
        del element["end_line"]
    assert yamlio.schema_errors(loc, "locations.schema.json") == []
    assert snapshot_check.locate(loc, doc["start_line"], doc["start_line"])["element_id_candidate"] is None
