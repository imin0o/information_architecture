"""スナップショットの不変性・ハッシュ範囲・整合検査（M1、付録B.1）。"""

import hashlib

import pytest

from iakit import cli, extract_cmd, snapshot, snapshot_check, yamlio
from iakit.paths import FIXTURES

PDF = FIXTURES / "pdf/page-labels.pdf"


def create(root, source=PDF, source_id="FIXTURE-page-labels"):
    return snapshot.create(source, source_id, root, "trial")


@pytest.mark.parametrize(("text", "start", "end", "expected"), [
    (b"a\nb\nc\n", 1, 1, b"a\n"),
    (b"a\nb\nc\n", 2, 3, b"b\nc\n"),
    (b"a\nb\nc", 3, 3, b"c"),  # 末尾LFのない最終行
    (b"a\n\nc\n", 2, 2, b"\n"),  # 空行も1行
    ("情報\n設計\n".encode(), 2, 2, "設計\n".encode()),
])
def test_excerpt_bytes_include_original_line_ends(text, start, end, expected):
    assert snapshot_check.excerpt_bytes(text, start, end) == expected


@pytest.mark.parametrize(("start", "end"), [(0, 1), (2, 1), (1, 4), (4, 4)])
def test_invalid_ranges_are_rejected(start, end):
    # 末尾LFの直後は追加の空行と数えないため、3行のテキストに4行目はない
    with pytest.raises(ValueError):
        snapshot_check.excerpt_bytes(b"a\nb\nc\n", start, end)


def test_reextraction_gets_new_id_and_keeps_old_snapshot(tmp_path):
    first = create(tmp_path)
    before = {p.name: p.read_bytes() for p in first.iterdir()}
    second = create(tmp_path)
    assert (first.name, second.name) == ("EXTRACT-FX-001", "EXTRACT-FX-002")
    assert {p.name: p.read_bytes() for p in first.iterdir()} == before
    assert (first / "text.txt").read_bytes() == (second / "text.txt").read_bytes()


def test_leftover_partial_directory_blocks_reuse_of_id(tmp_path):
    (tmp_path / ".EXTRACT-FX-001.partial").mkdir()
    assert create(tmp_path).name == "EXTRACT-FX-002"


def test_book_ids_get_their_own_sequence(tmp_path):
    create(tmp_path)
    assert snapshot.next_id(tmp_path, "BOOK-003") == "EXTRACT-003-001"
    with pytest.raises(ValueError):
        snapshot.next_id(tmp_path, "book-3")


def test_cross_page_excerpt_hash_and_locator(tmp_path):
    d = create(tmp_path)
    text = (d / "text.txt").read_bytes()
    loc = yamlio.load(d / "locations.yaml")
    p4, p5 = loc["pages"][3], loc["pages"][4]
    excerpt = snapshot_check.excerpt_bytes(text, p4["end_line"], p5["start_line"])
    assert excerpt.count(b"\n") == 2 and excerpt.endswith(b"\n")
    assert hashlib.sha256(excerpt).hexdigest() == hashlib.sha256(excerpt).hexdigest()
    found = snapshot_check.locate(loc, p4["end_line"], p5["start_line"])
    assert found["pdf_page_indices"] == [4, 5] and found["printed_page_labels"] == ["1", "2"]


def test_valid_snapshot_and_original_pass(tmp_path):
    assert snapshot_check.verify(create(tmp_path), PDF) == []


@pytest.mark.parametrize(("target", "mutate", "message"), [
    ("text.txt", lambda b: b.replace(b"Preface", b"Prefase"), "text.txt のハッシュ"),
    ("text.txt", lambda b: b.replace(b"\n", b"\r\n", 1), "正規化規則"),
    ("locations.yaml", lambda b: b.replace(b"end_line: 5\n", b"end_line: 4\n", 1), "locations.yaml のハッシュ"),
])
def test_tampering_is_detected(tmp_path, target, mutate, message):
    d = create(tmp_path)
    path = d / target
    path.write_bytes(mutate(path.read_bytes()))
    assert any(message in e for e in snapshot_check.verify(d))


def test_range_gap_and_wrong_original_are_detected(tmp_path):
    d = create(tmp_path)
    loc = yamlio.load(d / "locations.yaml")
    loc["pages"][1]["start_line"] += 1
    text = yamlio.dumps(loc)
    (d / "locations.yaml").write_text(text, encoding="utf-8", newline="\n")
    meta = yamlio.load(d / "extraction.yaml")
    meta["locations"]["sha256"] = snapshot.sha256(text.encode())
    (d / "extraction.yaml").write_text(yamlio.dumps(meta), encoding="utf-8", newline="\n")
    errors = snapshot_check.verify(d, FIXTURES / "epub/reflowable.epub")
    assert any("連続していない" in e for e in errors)
    assert any("原典の実ファイル" in e for e in errors)


def test_renamed_directory_is_detected(tmp_path):
    d = create(tmp_path)
    assert any("ディレクトリ名" in e for e in snapshot_check.verify(d.rename(tmp_path / "EXTRACT-FX-009")))


def test_preview_must_be_prefix_within_word_limit():
    excerpt = b"Synonyms map several labels to one concept.\n"
    assert snapshot_check.preview_errors(excerpt, "Synonyms map several") == []
    assert snapshot_check.preview_errors(excerpt, "map several") != []
    assert snapshot_check.preview_errors(b" ".join([b"w"] * 30), " ".join(["w"] * 26)) != []


def test_cli_output_contains_hashes_but_no_text(tmp_path, capsys):
    assert cli.main(["extract", "run", "--fixture", "pdf/page-labels.pdf", "--root", str(tmp_path)]) == 0
    assert cli.main(["excerpt", "ref", "--extraction", str(tmp_path / "EXTRACT-FX-001"), "--lines", "9-12"]) == 0
    out = capsys.readouterr().out
    assert "excerpt_sha256" in out and "Sorting things" not in out and "Preface" not in out


def rewrite_locations(directory, locations):
    raw = yamlio.dumps(locations)
    (directory / "locations.yaml").write_text(raw, encoding="utf-8", newline="\n")
    meta = yamlio.load(directory / "extraction.yaml")
    meta["locations"]["sha256"] = snapshot.sha256(raw.encode("utf-8"))
    (directory / "extraction.yaml").write_text(yamlio.dumps(meta), encoding="utf-8", newline="\n")


@pytest.mark.parametrize(("before", "after"), [
    ("BOOK-001", "BOOK-002"), ("BOOK-001", "FIXTURE-page-labels"), ("FIXTURE-page-labels", "BOOK-001"),
])
def test_source_id_must_match_extraction_id(tmp_path, before, after):
    d = create(tmp_path, source_id=before)
    meta = yamlio.load(d / "extraction.yaml")
    meta["source"]["source_id"] = after
    (d / "extraction.yaml").write_text(yamlio.dumps(meta), encoding="utf-8", newline="\n")
    assert any("source_id" in e for e in snapshot_check.verify(d, PDF))
    result = extract_cmd.ref(d, "1")
    assert result["result"] == "fail" and "source_id" not in result


@pytest.mark.parametrize("indices", [[999, 2, 3, 4, 5], [1, 1, 3, 4, 5], [2, 1, 3, 4, 5], [1, 2, 3, 4, 6]])
def test_pdf_indices_must_be_contiguous_even_after_rehashing(tmp_path, indices):
    d = create(tmp_path)
    loc = yamlio.load(d / "locations.yaml")
    for page, index in zip(loc["pages"], indices):
        page["pdf_page_index"] = index
    rewrite_locations(d, loc)
    assert any("PDF内のページ順" in e for e in snapshot_check.verify(d, PDF))
    assert extract_cmd.ref(d, "1")["result"] == "fail"


@pytest.mark.parametrize(("status", "start", "end"), [
    ("text", None, 2), ("text", 1, None), ("text", None, None),
    ("no_text", 1, 2), ("error", 1, None),
])
def test_invalid_status_ranges_fail_schema_without_raising(tmp_path, status, start, end):
    d = create(tmp_path)
    loc = yamlio.load(d / "locations.yaml")
    loc["pages"][0].update(status=status, start_line=start, end_line=end)
    rewrite_locations(d, loc)
    assert yamlio.schema_errors(loc, "locations.schema.json")
    assert snapshot_check.verify(d)
    assert extract_cmd.ref(d, "1")["result"] == "fail"


@pytest.mark.parametrize("source", [PDF, FIXTURES / "epub/reflowable.epub"])
def test_empty_location_units_fail_without_raising(tmp_path, source):
    d = create(tmp_path, source=source)
    loc = yamlio.load(d / "locations.yaml")
    loc["pages" if loc["format"] == "pdf" else "documents"] = []
    rewrite_locations(d, loc)
    assert snapshot_check.verify(d)


def test_missing_page_is_detected_by_unit_count(tmp_path):
    d = create(tmp_path)
    loc = yamlio.load(d / "locations.yaml")
    # 表紙の行を次ページへ含めても、抽出時の単位数との不一致は残る。
    loc["pages"].pop(0)
    loc["pages"][0]["start_line"] = 1
    for i, page in enumerate(loc["pages"], 1):
        page["pdf_page_index"] = i
    rewrite_locations(d, loc)
    assert any("単位数" in e for e in snapshot_check.verify(d))


@pytest.mark.parametrize("target", ["extraction.yaml", "locations.yaml", "missing-extraction"])
def test_invalid_snapshot_ref_returns_failure_report(tmp_path, capsys, target):
    d = create(tmp_path)
    if target == "missing-extraction":
        (d / "extraction.yaml").unlink()
    else:
        (d / target).write_text("{}", encoding="utf-8")
    report = tmp_path / "report.yaml"
    assert cli.main(["excerpt", "ref", "--extraction", str(d), "--lines", "1", "--report", str(report)]) == 1
    result = yamlio.load(report)
    assert result["result"] == "fail" and result["checks"][0]["errors"]
    assert "excerpt_sha256" not in result["checks"][0]
    assert yamlio.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("bad_end", [0, 999])
def test_element_end_line_must_be_within_document(tmp_path, bad_end):
    d = create(tmp_path, source=FIXTURES / "epub/reflowable.epub")
    loc = yamlio.load(d / "locations.yaml")
    loc["documents"][1]["elements"][0]["end_line"] = bad_end
    rewrite_locations(d, loc)
    assert snapshot_check.verify(d)
