"""EPUBのspine単位抽出（BR-01）。

本文化の規則は上流book-to-skillのstdlib抽出器（`_HTMLTextExtractor`）と同じにする。
script・style・headを除き、ブロック要素の開始・終了を改行、表のセルをタブで区切る。
これに加えて、要素IDが最初に本文を出す位置と、文字として読めない画像等を記録する。
リフロー表示のページ番号は作らない。
"""

import html.parser
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

METHOD = "iakit.epub_html (book-to-skill stdlib extractor rules)"
OPTIONS = {"order": "spine, then non-spine XHTML in manifest order", "leading_whitespace": "dropped",
           "postprocess": "none"}

SKIP_TAGS = {"script", "style", "head"}
BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "details", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody",
    "tfoot", "thead", "tr", "ul",
})
CELL_TAGS = frozenset({"td", "th"})
VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                       "source", "track", "wbr", "image"})
UNREADABLE = {"img": "image", "image": "image", "svg": "image", "video": "media",
              "audio": "media", "object": "media", "embed": "media", "math": "math"}


class TextExtractor(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.length = 0
        self.skip_depth = 0
        self.pending = ""
        self.stack: list[tuple[str, str | None]] = []
        self.waiting: list[str] = []
        self.anchors: dict[str, int] = {}
        self.unreadable: list[dict] = []

    def _mark(self, sep: str) -> None:
        if sep == "\n" or not self.pending:
            self.pending = sep

    def _nearest_id(self) -> str | None:
        return next((i for _, i in reversed(self.stack) if i), None)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id")
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if element_id and not self.skip_depth and element_id not in self.anchors:
            self.waiting.append(element_id)
        if tag in UNREADABLE and not self.skip_depth:
            self.unreadable.append({"element_id": element_id or self._nearest_id(), "kind": UNREADABLE[tag],
                                    "alt_text": bool((attrs.get("alt") or "").strip())})
        if tag not in VOID_TAGS:
            self.stack.append((tag, element_id))
        if tag in BLOCK_TAGS:
            self._mark("\n")
        elif tag in CELL_TAGS:
            self._mark("\t")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in BLOCK_TAGS:
            self._mark("\n")
        elif tag in CELL_TAGS:
            self._mark("\t")

    def handle_data(self, data):
        # 上流との差分: 本文が始まる前の空白だけのデータ（XML宣言やbody直後の改行）は出さない
        if self.skip_depth or (not self.parts and not data.strip()):
            return
        if self.pending:
            if not data.strip():
                return
            if self.parts:
                self._append(self.pending)
            self.pending = ""
        for element_id in self.waiting:
            self.anchors[element_id] = self.length
        self.waiting.clear()
        self._append(data)

    def _append(self, text: str) -> None:
        self.parts.append(text)
        self.length += len(text)

    def text(self) -> str:
        return "".join(self.parts)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _opf_path(zf: zipfile.ZipFile) -> str:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    for el in container.iter():
        if _local(el.tag) == "rootfile" and el.get("full-path"):
            return el.get("full-path")
    raise ValueError("container.xmlにrootfileがない")


def _documents(zf: zipfile.ZipFile, opf_path: str) -> list[dict]:
    opf = ET.fromstring(zf.read(opf_path))
    opf_dir = posixpath.dirname(opf_path)
    manifest: dict[str, dict] = {}
    order: list[dict] = []
    for el in opf.iter():
        name = _local(el.tag)
        if name == "item" and el.get("id") and el.get("href"):
            href = posixpath.normpath(unquote(urlsplit(el.get("href")).path))
            manifest[el.get("id")] = {"href": href, "media_type": el.get("media-type", "")}
        elif name == "itemref" and el.get("idref") in manifest:
            item = manifest[el.get("idref")]
            if item["href"] not in {d["href"] for d in order}:
                order.append({"href": item["href"], "in_spine": True, "linear": el.get("linear", "yes") != "no"})
    seen = {d["href"] for d in order}
    for item in manifest.values():
        if item["href"] not in seen and re.search(r"\.x?html?$", item["href"]):
            order.append({"href": item["href"], "in_spine": False, "linear": None})
    for doc in order:
        doc["member"] = posixpath.normpath(posixpath.join(opf_dir, doc["href"])) if opf_dir else doc["href"]
    return order


def extract(path: Path) -> dict:
    """spine順の文書ごとに本文・要素IDの位置・読めない要素を返す。"""
    warnings: list[str] = []
    units = []
    with zipfile.ZipFile(path) as zf:
        for doc in _documents(zf, _opf_path(zf)):
            unit = {"href": doc["href"], "in_spine": doc["in_spine"], "linear": doc["linear"],
                    "text": "", "anchors": {}, "unreadable": [], "error": None}
            try:
                raw = zf.read(doc["member"])
            except KeyError:
                unit["error"] = "manifestのファイルがアーカイブにない"
                units.append(unit)
                continue
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError:
                source = raw.decode("utf-8", errors="replace")
                warnings.append(f"{doc['href']}: UTF-8として読めないバイトを置換した")
            parser = TextExtractor()
            parser.feed(source)
            parser.close()
            unit.update(text=parser.text(), anchors=parser.anchors, unreadable=parser.unreadable)
            units.append(unit)
    return {
        "format": "epub",
        "method": METHOD,
        "libraries": {},
        "options": dict(OPTIONS),
        "page_labels": "absent",
        "units": units,
        "warnings": warnings,
    }
