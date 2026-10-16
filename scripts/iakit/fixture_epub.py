"""自作EPUB素材の生成。spine項目のhrefと要素IDで位置を参照する例を作る。

zipの日時を固定し、無圧縮で格納するため、同じ入力からは常に同じファイルになる。
"""

import io
import zipfile

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000001</dc:identifier>
    <dc:title>Fixture Reflowable Book</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="fig1" href="images/fig1.svg" media-type="image/svg+xml"/>
  </manifest>
  <spine>
    <itemref idref="nav" linear="no"/>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""


def _xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"<head><title>{title}</title></head>\n<body>\n{body}</body>\n</html>\n"
    )


NAV = _xhtml("Contents", """<nav epub:type="toc"><ol>
  <li><a href="text/ch1.xhtml#ch1">1 Labels and concepts</a></li>
  <li><a href="text/ch2.xhtml#ch2">2 Paths through content</a></li>
</ol></nav>
""")

CH1 = _xhtml("1 Labels and concepts", """<section id="ch1">
<h1>1 Labels and concepts</h1>
<p id="p-1-1">A label is not an identifier. Two concepts may share the label "bank".</p>
<section id="sec-1-1">
<h2>1.1 Synonyms</h2>
<p id="p-1-1-1">Synonyms map several labels to one concept.</p>
<p>This paragraph has no element ID, so only the spine href locates it.</p>
</section>
</section>
""")

CH2 = _xhtml("2 Paths through content", """<section id="ch2">
<h1>2 Paths through content</h1>
<p id="p-2-1">A figure follows. Its content is an image and cannot be read as text.</p>
<figure id="fig-2-1"><img src="../images/fig1.svg" alt=""/><figcaption>Figure 2.1</figcaption></figure>
<table id="tbl-2-1"><tr><th>Path</th><th>Steps</th></tr><tr><td>Browse</td><td>3</td></tr></table>
</section>
""")

FIG = """<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20"/></svg>
"""

# 先頭の mimetype はEPUBの規定どおり最初に置く
ENTRIES = [
    ("mimetype", "application/epub+zip"),
    ("META-INF/container.xml", CONTAINER),
    ("OEBPS/content.opf", OPF),
    ("OEBPS/nav.xhtml", NAV),
    ("OEBPS/text/ch1.xhtml", CH1),
    ("OEBPS/text/ch2.xhtml", CH2),
    ("OEBPS/images/fig1.svg", FIG),
]


def build() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, text in ENTRIES:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0
            zf.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def expected() -> dict:
    """M1の位置対応表を検査するための正解（本文は自作）。"""
    return {
        "spine": [
            {"href": "nav.xhtml", "linear": False},
            {"href": "text/ch1.xhtml", "linear": True, "element_ids": ["ch1", "p-1-1", "sec-1-1", "p-1-1-1"]},
            {"href": "text/ch2.xhtml", "linear": True, "element_ids": ["ch2", "p-2-1", "fig-2-1", "tbl-2-1"]},
        ],
        "unreadable": [{"href": "text/ch2.xhtml", "element_id": "fig-2-1", "kind": "image"}],
        "paragraph_without_id": {"href": "text/ch1.xhtml", "starts_with": "This paragraph has no element ID"},
        "printed_page_numbers": None,
    }
