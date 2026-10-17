"""固定抽出スナップショットの作成（BR-01、付録B.1）。

`<root>/<extraction_id>/` に text.txt、locations.yaml、extraction.yaml を保存する。
IDは不変とし、再抽出には新しいIDを割り当てる。既存のディレクトリは上書きしない。
"""

import hashlib
import os
import platform
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, extract_epub, extract_pdf, leak_rules, yamlio

SCHEMA_VERSION = "0.1.0"
NORMALIZATION = {
    "id": "iakit-norm-1", "encoding": "utf-8", "bom": False, "newline": "lf",
    "unicode": "none", "unit_final_newline": True,
}
UPSTREAM = {
    "repository": "https://github.com/virgiliojr94/book-to-skill",
    "commit": "a24e62f4d8caf2ee433f6e48d9073800f56f906e",
    "license": "MIT",
    "code_executed": False,
}
ID_PATTERN = re.compile(r"^EXTRACT-([A-Z0-9]+)-(\d{3})$")
INVISIBLE = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x034F, 0x180E, 0x200E, 0x200F,
                       0x061C, *range(0x202A, 0x202F), *range(0x2061, 0x2065), *range(0x2066, 0x206A)})


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_format(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "pdf"
    if leak_rules.book_content(content):
        return "epub"
    raise ValueError("PDF・EPUBとして判定できない")


def source_tag(source_id: str) -> str:
    match = re.fullmatch(r"BOOK-(\d{3})", source_id)
    if match:
        return match.group(1)
    if re.fullmatch(r"FIXTURE-[a-z0-9-]+", source_id):
        return "FX"
    raise ValueError(f"source_idの形式が不正: {source_id}")


def next_id(root: Path, source_id: str) -> str:
    tag = source_tag(source_id)
    used = [int(m.group(2)) for p in root.glob(f"*EXTRACT-{tag}-*")
            if (m := ID_PATTERN.match(p.name.lstrip(".").removesuffix(".partial")))]
    return f"EXTRACT-{tag}-{max(used, default=0) + 1:03d}"


def _normalize(text: str, counts: dict) -> str:
    counts["crlf"] += text.count("\r\n")
    text = text.replace("\r\n", "\n")
    counts["cr"] += text.count("\r")
    return text.replace("\r", "\n")


def _element_lines(raw: str, text: str, anchors: dict, line: int) -> list[dict]:
    """要素内に全文が収まる行だけを候補範囲にする。部分行と空要素は推測しない。"""
    elements = []
    for element_id, (start, end) in anchors.items():
        start = len(_normalize(raw[:start], {"crlf": 0, "cr": 0}))
        end = len(_normalize(raw[:end], {"crlf": 0, "cr": 0}))
        first = text.count("\n", 0, start)
        last = text.count("\n", 0, end - 1)
        if text[text.rfind("\n", 0, start) + 1:start].strip():
            first += 1
        stop = text.find("\n", end)
        if text[end - 1] != "\n" and text[end:stop if stop >= 0 else len(text)].strip():
            last -= 1
        if first <= last:
            elements.append({"id": element_id, "line": line + first, "end_line": line + last})
    return elements


def assemble(extracted: dict) -> tuple[str, list[dict], dict]:
    """各単位を改行LFで連結し、単位ごとの行範囲を求める。単位の間に区切り行は入れない。"""
    counts = {"crlf": 0, "cr": 0}
    chunks, entries, line = [], [], 1
    for unit in extracted["units"]:
        text = _normalize(unit["text"], counts)
        entry = {k: v for k, v in unit.items() if k not in ("text", "anchors")}
        if text.strip():
            if not text.endswith("\n"):
                text += "\n"
            n = text.count("\n")
            entry.update(start_line=line, end_line=line + n - 1, status="text")
            if "anchors" in unit:
                entry["elements"] = _element_lines(unit["text"], text, unit["anchors"], line)
            chunks.append(text)
            line += n
        else:
            entry.update(start_line=None, end_line=None, status="error" if unit.get("error") else "no_text")
            if "anchors" in unit:
                entry["elements"] = []
        entries.append(entry)
    return "".join(chunks), entries, counts


def _quality(text: str, entries: list[dict], extracted: dict, counts: dict) -> dict:
    return {
        "units": len(entries),
        "units_without_text": sum(e["status"] != "text" for e in entries),
        "unit_errors": sum(e["status"] == "error" for e in entries),
        "unreadable_elements": sum(len(e.get("unreadable", [])) + e.get("images", 0) for e in entries),
        "invisible_codepoints": sum(ord(c) in INVISIBLE for c in text),
        "replacement_chars": text.count("�"),
        "newlines_converted": {"crlf": counts["crlf"], "cr": counts["cr"]},
        "warnings": extracted["warnings"][:50],
        "warning_count": len(extracted["warnings"]),
        "sample_review": "not_run",
    }


def create(source: Path, source_id: str, root: Path, bibliography_state: str) -> Path:
    """抽出してスナップショットを保存し、そのディレクトリを返す。"""
    content = source.read_bytes()
    fmt = detect_format(content)
    extracted = (extract_pdf if fmt == "pdf" else extract_epub).extract(source)
    text, entries, counts = assemble(extracted)
    text_bytes = text.encode("utf-8")
    root.mkdir(parents=True, exist_ok=True)
    extraction_id = next_id(root, source_id)
    final = root / extraction_id
    partial = root / f".{extraction_id}.partial"
    if final.exists() or partial.exists():
        raise FileExistsError(f"{extraction_id} は既に存在する")
    unit_key = "pages" if fmt == "pdf" else "documents"
    locations = {
        "schema_version": SCHEMA_VERSION, "extraction_id": extraction_id, "text_sha256": sha256(text_bytes),
        "format": fmt, "page_labels": extracted["page_labels"], unit_key: entries,
        "printed_page_numbers": "from_page_labels" if extracted["page_labels"] == "present" else None,
    }
    locations_text = yamlio.dumps(locations)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "extraction_id": extraction_id,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"source_id": source_id, "sha256": sha256(content), "format": fmt,
                   "size_bytes": len(content), "bibliography_state": bibliography_state},
        "extractor": {
            "adapter": f"iakit {__version__}", "method": extracted["method"],
            "python": platform.python_version(), "libraries": extracted["libraries"],
            "options": extracted["options"], "fallback": None, "llm_calls": 0, "upstream": UPSTREAM,
        },
        "normalization": NORMALIZATION,
        "text": {"path": "text.txt", "sha256": sha256(text_bytes), "bytes": len(text_bytes),
                 "lines": text.count("\n")},
        "locations": {"path": "locations.yaml", "sha256": sha256(locations_text.encode("utf-8"))},
        "quality": _quality(text, entries, extracted, counts),
    }
    partial.mkdir()
    try:
        (partial / "text.txt").write_bytes(text_bytes)
        (partial / "locations.yaml").write_text(locations_text, encoding="utf-8", newline="\n")
        (partial / "extraction.yaml").write_text(yamlio.dumps(meta), encoding="utf-8", newline="\n")
        os.rename(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return final
