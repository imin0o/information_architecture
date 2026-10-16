"""自作PDF素材の生成。PDF内のページ順と印刷ページ番号（PageLabels）が異なる例を作る。

外部ライブラリを使わずにバイト列を組み立てるため、同じ入力からは常に同じファイルになる。
"""

# (印刷ページ番号, 本文行)。索引0は表紙で印刷番号を持たない。
PAGES = [
    (None, ["Fixture Handbook", "A self-written test document. Not a real book."]),
    ("i", ["Preface", "Front matter uses roman page numbers.", "PDF page index 2 is printed as i."]),
    ("ii", ["Contents", "1 Sorting things ... 1", "2 Finding things ... 2"]),
    ("1", ["1 Sorting things", "Arabic numbering restarts at 1 on PDF page index 4.",
           "This paragraph continues on the next page and tests a range that spans two"]),
    ("2", ["pages without a heading at the top.", "2 Finding things",
           "Hyphenated words at line ends are kept as written: classi-", "fication."]),
]
PAGE_LABELS = "<< /Nums [0 << /P (Cover) >> 1 << /S /r >> 3 << /S /D >>] >>"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: list[str]) -> bytes:
    ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for line in lines:
        ops.append(f"({_escape(line)}) Tj T*")
    ops.append("ET")
    return ("\n".join(ops) + "\n").encode("ascii")


def build() -> bytes:
    n = len(PAGES)
    # 1: Catalog, 2: Pages, 3: Font, 4..: Page/Content の組
    page_ids = [4 + 2 * i for i in range(n)]
    objects: dict[int, bytes] = {
        1: f"<< /Type /Catalog /Pages 2 0 R /PageLabels {PAGE_LABELS} >>".encode("ascii"),
        2: f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n} >>".encode("ascii"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    for i, (_, lines) in enumerate(PAGES):
        page_id, content_id = page_ids[i], page_ids[i] + 1
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects[content_id] = stream(_content_stream(lines))
    return assemble(objects)


def stream(data: bytes, extra: str = "") -> bytes:
    return b"<< %s/Length %d >>\nstream\n" % (extra.encode("ascii"), len(data)) + data + b"endstream"


def assemble(objects: dict[int, bytes]) -> bytes:
    """オブジェクト番号順に本体・xref・trailerを組み立てる。1番をCatalogとする。"""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += b"%d 0 obj\n" % obj_id + objects[obj_id] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for obj_id in range(1, size):
        out += b"%010d 00000 n \n" % offsets[obj_id]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at)
    return bytes(out)


def expected() -> dict:
    """M1の位置対応表を検査するための正解（本文は自作）。"""
    return {
        "page_count": len(PAGES),
        "pages": [
            {"pdf_page_index": i + 1, "printed_label": label or "Cover", "first_line": lines[0]}
            for i, (label, lines) in enumerate(PAGES)
        ],
        "cross_page_range": {"from_pdf_page_index": 4, "to_pdf_page_index": 5},
    }
