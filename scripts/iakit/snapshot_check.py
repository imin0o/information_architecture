"""固定抽出スナップショットの検査と抜粋範囲の扱い（付録B.1）。

抜粋のバイト列は、LFで区切った1始まりの行のうち start..end（両端を含む）を、
各行に元から付いている末尾LFごと取り出したもの。照合時に空白・改行・Unicodeを変換しない。
"""

from pathlib import Path

from . import yamlio
from .snapshot import ID_PATTERN, sha256, source_tag

PREVIEW_MAX_WORDS = 25


def line_spans(text: bytes) -> list[tuple[int, int]]:
    """各行の [開始, 終了) バイト位置。末尾LFの直後は追加の空行と数えない。"""
    spans, start = [], 0
    while start < len(text):
        end = text.find(b"\n", start)
        end = len(text) if end < 0 else end + 1
        spans.append((start, end))
        start = end
    return spans


def excerpt_bytes(text: bytes, start_line: int, end_line: int) -> bytes:
    spans = line_spans(text)
    if not (1 <= start_line <= end_line <= len(spans)):
        raise ValueError(f"行範囲 {start_line}-{end_line} が不正（全{len(spans)}行）")
    return text[spans[start_line - 1][0]:spans[end_line - 1][1]]


def preview_errors(excerpt: bytes, preview: str) -> list[str]:
    errors = []
    if not excerpt.decode("utf-8").startswith(preview) or not preview:
        errors.append("excerpt_preview が範囲の先頭からの連続部分ではない")
    if len(preview.split()) > PREVIEW_MAX_WORDS:
        errors.append(f"excerpt_preview が{PREVIEW_MAX_WORDS}語を超える")
    return errors


def locate(locations: dict, start_line: int, end_line: int) -> dict:
    """行範囲に対応する位置の候補。人が原典で確認するまで provenance の locator に確定させない。"""
    if locations["format"] == "pdf":
        pages = [p for p in locations["pages"] if p["status"] == "text"
                 and p["start_line"] <= end_line and p["end_line"] >= start_line]
        labels = [p["printed_page_label"] for p in pages]
        return {
            "pdf_page_indices": [p["pdf_page_index"] for p in pages],
            "printed_page_labels": labels if labels and None not in labels else [],
            "printed_page_labels_source": locations["printed_page_numbers"],
        }
    docs = [d for d in locations["documents"] if d["status"] == "text"
            and d["start_line"] <= end_line and d["end_line"] >= start_line]
    if len(docs) != 1:
        return {"epub_href": None, "element_id_candidate": None, "note": f"{len(docs)}文書にまたがる"}
    containing = [e for e in docs[0]["elements"]
                  if "end_line" in e and e["line"] <= start_line <= end_line <= e["end_line"]]
    element = min(reversed(containing), key=lambda e: e["end_line"] - e["line"], default=None)
    return {"epub_href": docs[0]["href"],
            "element_id_candidate": element["id"] if element else None}


def _range_errors(units: list[dict], lines: int, key: str) -> list[str]:
    errors, expected = [], 1
    for u in units:
        if u["status"] != "text":
            if u["start_line"] is not None or u["end_line"] is not None:
                errors.append(f"{u[key]}: 本文のない単位に行範囲がある")
            continue
        if u["start_line"] != expected or u["end_line"] < u["start_line"]:
            errors.append(f"{u[key]}: 行範囲が連続していない（{u['start_line']}-{u['end_line']}、期待する開始{expected}）")
        for e in u.get("elements", []):
            if not u["start_line"] <= e["line"] <= e.get("end_line", e["line"]) <= u["end_line"]:
                errors.append(f"{u[key]}#{e['id']}: 要素の行が文書の範囲外")
        expected = u["end_line"] + 1
    if expected - 1 != lines:
        errors.append(f"対応表が全{lines}行を覆っていない（{expected - 1}行まで）")
    return errors


def verify(directory: Path, original: Path | None = None) -> list[str]:
    """スナップショットの自己整合を検査する。original があれば原典ハッシュも照合する。"""
    errors: list[str] = []
    try:
        meta = yamlio.load(directory / "extraction.yaml")
        locations_raw = (directory / "locations.yaml").read_bytes()
        locations = yamlio.loads(locations_raw.decode("utf-8"))
        text = (directory / "text.txt").read_bytes()
    except (OSError, ValueError) as exc:
        return [f"{directory.name}: 読込み失敗: {exc}"]
    errors += [f"extraction.yaml {e}" for e in yamlio.schema_errors(meta, "extraction.schema.json")]
    errors += [f"locations.yaml {e}" for e in yamlio.schema_errors(locations, "locations.schema.json")]
    if errors:
        return errors
    if meta["extraction_id"] != directory.name or locations["extraction_id"] != directory.name:
        errors.append("extraction_id がディレクトリ名と一致しない")
    if ID_PATTERN.fullmatch(meta["extraction_id"])[1] != source_tag(meta["source"]["source_id"]):
        errors.append("source_id と extraction_id の出典が一致しない")
    if sha256(text) != meta["text"]["sha256"] or locations["text_sha256"] != meta["text"]["sha256"]:
        errors.append("text.txt のハッシュが記録と一致しない")
    if sha256(locations_raw) != meta["locations"]["sha256"]:
        errors.append("locations.yaml のハッシュが記録と一致しない")
    if text.startswith(b"\xef\xbb\xbf") or b"\r" in text or (text and not text.endswith(b"\n")):
        errors.append("text.txt が正規化規則（BOMなし・LF・末尾LF）に合わない")
    try:
        text.decode("utf-8")
    except UnicodeDecodeError:
        errors.append("text.txt がUTF-8ではない")
    lines = len(line_spans(text))
    if lines != meta["text"]["lines"] or len(text) != meta["text"]["bytes"]:
        errors.append("text.txt の行数・バイト数が記録と一致しない")
    if meta["source"]["format"] != locations["format"]:
        errors.append("形式が extraction.yaml と locations.yaml で異なる")
    key = "pdf_page_index" if locations["format"] == "pdf" else "href"
    units = locations["pages" if locations["format"] == "pdf" else "documents"]
    if locations["format"] == "pdf" and [p[key] for p in units] != list(range(1, len(units) + 1)):
        errors.append("PDF内のページ順が1始まりの連番ではない")
    if len(units) != meta["quality"]["units"]:
        errors.append("対応表の単位数が抽出記録と一致しない")
    errors += _range_errors(units, lines, key)
    if original is not None and sha256(original.read_bytes()) != meta["source"]["sha256"]:
        errors.append("原典の実ファイルのハッシュが記録と一致しない")
    return errors
