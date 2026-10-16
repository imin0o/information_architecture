"""自作PDF回帰素材。Unicode・合字・2段組・埋め込みグリフ（Type3）の抽出を固定する。

1ページ目: CIDフォント（Identity-H）とToUnicodeで、日本語、合成済み・分解済みの文字、
合字グリフ（U+FB01）と複数文字へ対応するグリフ、全角英数字、康熙部首を並べる。
2ページ目: 左段を書き終えてから右段を書く2段組。
3ページ目: 字形をPDF内で定義したType3フォント。字形は四角形で、文字はToUnicodeだけが表す。
"""

from .fixture_pdf import assemble, stream

# 1ページ目の各行。タプルは1グリフで複数文字（または合字1文字）を表す。
CID_LINES = [
    list("情報設計の自作試験"),
    list("Precomposed: caf") + ["é"] + list(" / decomposed: cafe") + ["́"],
    list("Ligatures: ") + ["ﬁ"] + list("nd o") + [("ffi",)] + list("ce"),
    list("Width: ") + ["Ａ", "１"] + list(" Radical: ") + ["⼈"],
]
LEFT = ["Left column line one", "Left column line two"]
RIGHT = ["Right column line one", "Right column line two"]
TYPE3_LINE = "Embedded glyphs"


def _glyph_table() -> dict[str, int]:
    glyphs: dict[str, int] = {}
    for line in CID_LINES:
        for g in line:
            key = g[0] if isinstance(g, tuple) else g
            glyphs.setdefault(key, len(glyphs) + 1)
    return glyphs


def _utf16_hex(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def _to_unicode(pairs: list[tuple[int, str]], code_bytes: int) -> bytes:
    width = code_bytes * 2
    body = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Fixture-UCS def", "/CMapType 2 def", "1 begincodespacerange",
        f"<{'0' * width}> <{'F' * width}>", "endcodespacerange", f"{len(pairs)} beginbfchar",
    ]
    body += [f"<{code:0{width}X}> <{_utf16_hex(text)}>" for code, text in pairs]
    body += ["endbfchar", "endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return ("\n".join(body) + "\n").encode("ascii")


def _cid_page(glyphs: dict[str, int]) -> bytes:
    ops = ["BT", "/F2 12 Tf", "16 TL", "72 720 Td"]
    for line in CID_LINES:
        codes = "".join(f"{glyphs[g[0] if isinstance(g, tuple) else g]:04X}" for g in line)
        ops.append(f"<{codes}> Tj T*")
    return ("\n".join(ops + ["ET"]) + "\n").encode("ascii")


def _columns_page() -> bytes:
    ops = []
    for x, lines in ((72, LEFT), (320, RIGHT)):
        ops += ["BT", "/F1 12 Tf", "14 TL", f"{x} 720 Td"] + [f"({t}) Tj T*" for t in lines] + ["ET"]
    return ("\n".join(ops) + "\n").encode("ascii")


def _type3_page() -> bytes:
    codes = "".join(f"{i + 1:02X}" for i in range(len(TYPE3_LINE)))
    return f"BT\n/F3 12 Tf\n72 720 Td\n<{codes}> Tj\nET\n".encode("ascii")


def build() -> bytes:
    glyphs = _glyph_table()
    # 1: Catalog, 2: Pages, 3-5: Page, 6-8: Contents, 9: Helvetica, 10-13: CIDフォント, 14-16: Type3
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
        6: stream(_cid_page(glyphs)),
        7: stream(_columns_page()),
        8: stream(_type3_page()),
        9: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        10: b"<< /Type /Font /Subtype /Type0 /BaseFont /FixtureCID /Encoding /Identity-H "
            b"/DescendantFonts [11 0 R] /ToUnicode 13 0 R >>",
        11: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /FixtureCID /CIDToGIDMap /Identity "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            b"/FontDescriptor 12 0 R /DW 1000 >>",
        12: b"<< /Type /FontDescriptor /FontName /FixtureCID /Flags 4 /FontBBox [0 -200 1000 900] "
            b"/ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>",
        13: stream(_to_unicode([(c, g) for g, c in glyphs.items()], 2)),
        14: b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 600 700] /FontMatrix [0.001 0 0 0.001 0 0] "
            b"/CharProcs 15 0 R /Encoding << /Type /Encoding /Differences [1 "
            + " ".join(f"/g{i + 1}" for i in range(len(TYPE3_LINE))).encode("ascii")
            + b"] >> /FirstChar 1 /LastChar %d /Widths [%s] /Resources << >> /ToUnicode 16 0 R >>"
            % (len(TYPE3_LINE), " ".join(["600"] * len(TYPE3_LINE)).encode("ascii")),
        16: stream(_to_unicode([(i + 1, ch) for i, ch in enumerate(TYPE3_LINE)], 1)),
    }
    box = stream(b"600 0 0 0 600 700 d1\n50 0 500 700 re f\n")
    char_procs = []
    for i in range(len(TYPE3_LINE)):
        objects[17 + i] = box
        char_procs.append(f"/g{i + 1} {17 + i} 0 R")
    objects[15] = ("<< " + " ".join(char_procs) + " >>").encode("ascii")
    fonts = "/Font << /F1 9 0 R /F2 10 0 R /F3 14 0 R >>"
    for page_id, content_id in ((3, 6), (4, 7), (5, 8)):
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << {fonts} >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
    return assemble(objects)


def expected() -> dict:
    """抽出で得るべき各ページの行。Unicode正規化・合字展開・部首の変換を行わない。"""
    cid_text = ["".join(g[0] if isinstance(g, tuple) else g for g in line) for line in CID_LINES]
    return {
        "page_count": 3,
        "page_labels": None,
        "pages": [
            {"pdf_page_index": 1, "lines": cid_text},
            {"pdf_page_index": 2, "lines": LEFT + RIGHT},
            {"pdf_page_index": 3, "lines": [TYPE3_LINE]},
        ],
    }
