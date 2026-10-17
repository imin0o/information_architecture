"""PDF・DOCX・XML・SKOS（RDF/XML）の取込み。位置子の形式は ingest.py の説明を参照する。

XMLはDOCTYPE宣言を含むものを解析しない（外部実体・実体展開を避ける）。SKOSはRDF/XMLだけを扱い、
Turtle等は読めない部分として記録する。PDFの印刷ページ番号は推測せず、物理ページ順だけを使う。
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .ingest import unit, unreadable

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
SKOS = "{http://www.w3.org/2004/02/skos/core#}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
DOCTYPE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


def _xml(data: bytes, name: str, locator: str):
    if DOCTYPE.search(data):
        return None, [unreadable(locator, "parse_error", "DOCTYPE・実体宣言を含むXMLは安全のため解析しない")]
    try:
        return ET.fromstring(data), []
    except ET.ParseError as exc:
        return None, [unreadable(locator, "parse_error", f"XMLとして解析できない: {exc}")]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_units(root, name: str) -> list[dict]:
    units = []

    def walk(el, path):
        text = (el.text or "").strip()
        attrs = {_local(k): v for k, v in el.attrib.items()}
        if text or attrs:
            units.append(unit(f"{name}#xpath={path}", "element", text or None, fields=attrs or None))
        counts: dict[str, int] = {}
        for child in el:
            tag = _local(child.tag)
            counts[tag] = counts.get(tag, 0) + 1
            walk(child, f"{path}/{tag}[{counts[tag]}]")

    walk(root, f"/{_local(root.tag)}")
    return units


def _skos(root, name: str) -> tuple[list[dict], list[dict]]:
    units, bad = [], []
    concepts = [el for el in root.iter() if el.tag == SKOS + "Concept" or (
        el.tag == RDF + "Description" and any(t.get(RDF + "resource") == SKOS[1:-1] + "Concept"
                                              for t in el.findall(RDF + "type")))]
    if not concepts:
        return [], [unreadable(f"{name}#/", "parse_error", "skos:Concept が見つからない（RDF/XMLのSKOSだけを扱う）")]
    for n, c in enumerate(concepts, 1):
        about = c.get(RDF + "about")
        if not about:
            bad.append(unreadable(f"{name}#concept-index={n}", "parse_error", "rdf:about のない概念は位置を特定できない"))
            continue
        fields: dict = {}
        for el in c:
            key = _local(el.tag)
            if el.tag.startswith(SKOS) and key in ("prefLabel", "altLabel", "hiddenLabel", "scopeNote", "definition"):
                fields.setdefault(key, []).append({"lang": el.get(XML_LANG), "text": (el.text or "").strip()})
            elif el.tag.startswith(SKOS) and key in ("broader", "narrower", "related", "inScheme", "topConceptOf"):
                fields.setdefault(key, []).append(el.get(RDF + "resource"))
        units.append(unit(f"{name}#concept={about}", "concept", fields=fields))
    return units, bad


def _pdf(data: bytes, name: str) -> tuple[list[dict], list[dict]]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        return [], [unreadable(f"{name}#page=1", "parse_error", f"PDFとして読めない: {exc}")]
    units, bad = [], []
    for p, page in enumerate(pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf はページ単位で多様な例外を出す
            bad.append(unreadable(f"{name}#page={p}", "parse_error", f"ページの文字を取り出せない: {type(exc).__name__}"))
            continue
        lines = text.splitlines()
        if not any(ln.strip() for ln in lines):
            bad.append(unreadable(f"{name}#page={p}", "image", "文字層がない。画像だけのページの可能性がある"))
        for n, line in enumerate(lines, 1):
            if line.strip():
                units.append(unit(f"{name}#page={p}&L{n}", "line", line))
        try:
            if len(page.images):
                bad.append(unreadable(f"{name}#page={p}", "figure", f"画像{len(page.images)}点の内容は取り込んでいない"))
        except Exception:  # 画像の列挙に失敗しても本文の取込みは続ける
            bad.append(unreadable(f"{name}#page={p}", "other", "画像の有無を確認できない"))
    return units, bad


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t"))


def _docx(data: bytes, name: str) -> tuple[list[dict], list[dict]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        return [], [unreadable(f"{name}#p=1", "parse_error", f"DOCXとして読めない: {exc}")]
    root, bad = _xml(xml, name, f"{name}#p=1")
    if root is None:
        return [], bad
    body = root.find(W + "body")
    units, p, t = [], 0, 0
    for el in body if body is not None else []:
        if el.tag == W + "p":
            p += 1
            style = el.find(f"{W}pPr/{W}pStyle")
            heading = style is not None and style.get(W + "val", "").lower().startswith(("heading", "title"))
            if el.find(f".//{W}drawing") is not None or el.find(f".//{W}pict") is not None:
                bad.append(unreadable(f"{name}#p={p}", "image", "段落内の画像・図形は取り込んでいない"))
            if _text(el).strip():
                units.append(unit(f"{name}#p={p}", "heading" if heading else "paragraph", _text(el)))
        elif el.tag == W + "tbl":
            t += 1
            for r, row in enumerate(el.findall(W + "tr"), 1):
                for c, cell in enumerate(row.findall(W + "tc"), 1):
                    if _text(cell).strip():
                        units.append(unit(f"{name}#table={t}&row={r}&cell={c}", "cell", _text(cell)))
    return units, bad


def parse(data: bytes, name: str, fmt: str) -> tuple[list[dict], list[dict]]:
    if fmt == "pdf":
        return _pdf(data, name)
    if fmt == "docx":
        return _docx(data, name)
    root, bad = _xml(data, name, f"{name}#xpath=/")
    if root is None:
        if fmt == "skos" and not data.lstrip().startswith(b"<"):
            bad = [unreadable(f"{name}#/", "parse_error", "RDF/XML以外のSKOS（Turtle等）は未対応。RDF/XMLに変換して登録する")]
        return [], bad
    return _skos(root, name) if fmt == "skos" else (_xml_units(root, name), [])
