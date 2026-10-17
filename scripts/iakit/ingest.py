"""案件の入力を位置付きの単位へ分ける取込み（IN-01、IN-06）。

書籍の固定抽出（extract_*.py）とは責任を分ける。こちらは実行ごとの入力を対象にし、
位置子は「保存したファイル名#位置」の形にする。読めない部分は unreadable に記録し、
推測で埋めない。資料内の命令文は flags で注意を促すだけで、実行・送信の根拠にしない。

位置子の形式:
  text・markdown  name#L<行>
  csv             name#row=<行>（見出し行を1とする）
  json・yaml      name#/<JSON Pointer>
  xml             name#xpath=/a/b[2]
  skos            name#concept=<rdf:about>（RDF/XMLのみ）
  pdf             name#page=<物理ページ順>&L<行>
  docx            name#p=<段落>、表は name#table=<表>&row=<行>&cell=<セル>
"""

import csv
import io
import json
import re
from pathlib import Path

from . import yamlio

FORMATS = {
    ".txt": "text", ".md": "markdown", ".markdown": "markdown", ".pdf": "pdf", ".docx": "docx", ".csv": "csv",
    ".xml": "xml", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".rdf": "skos", ".skos": "skos",
}
INSTRUCTION = re.compile(
    r"(送信|転送|アップロード|共有して|実行して|削除して|無視して|従って|公開して"
    r"|ignore (all|any|previous|the above)|send (it|this|the)|upload|execute|run the|disregard)",
    re.IGNORECASE)


def guess_format(path: Path) -> str:
    try:
        return FORMATS[path.suffix.lower()]
    except KeyError:
        raise ValueError(f"{path.name}: 拡張子から形式を判定できない。--format で指定する") from None


def unit(locator: str, kind: str, text: str | None = None, **extra) -> dict:
    u = {"locator": locator, "kind": kind}
    if text is not None:
        u["text"] = text
    u.update({k: v for k, v in extra.items() if v})
    sample = text if text is not None else json.dumps(extra.get("fields", {}), ensure_ascii=False)
    if INSTRUCTION.search(sample):
        u["flags"] = ["instruction_like"]
    return u


def unreadable(locator: str, kind: str, note: str) -> dict:
    return {"locator": locator, "kind": kind, "note": note}


def decode(data: bytes, name: str) -> tuple[str | None, list[dict]]:
    try:
        return data.decode("utf-8-sig"), []
    except UnicodeDecodeError as exc:
        return None, [unreadable(f"{name}#byte={exc.start}", "encoding", "UTF-8として復号できない。全体を読めていない")]


def _lines(text: str, name: str, markdown: bool) -> list[dict]:
    units, headings, fence = [], [], False
    for n, line in enumerate(text.splitlines(), 1):
        if markdown and line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = re.match(r"^(#{1,6})\s+(.*)$", line) if markdown and not fence else None
        if m:
            headings = headings[:len(m.group(1)) - 1] + [m.group(2).strip()]
            units.append(unit(f"{name}#L{n}", "heading", m.group(2).strip(), heading_path=list(headings)))
        elif line.strip():
            units.append(unit(f"{name}#L{n}", "line", line, heading_path=list(headings) if markdown else None))
    return units


def _csv(text: str, name: str) -> tuple[list[dict], list[dict]]:
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        return [], []
    header, units, bad = rows[0], [unit(f"{name}#row=1", "header", ",".join(rows[0]))], []
    for n, row in enumerate(rows[1:], 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            bad.append(unreadable(f"{name}#row={n}", "parse_error", f"列数が見出し行（{len(header)}列）と異なる（{len(row)}列）"))
            continue
        units.append(unit(f"{name}#row={n}", "row", fields=dict(zip(header, row))))
    return units, bad


def _pointer(parts: list) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts) if parts else ""


def _walk(value, name: str, parts: list) -> list[dict]:
    if isinstance(value, dict):
        return [u for k, v in value.items() for u in _walk(v, name, parts + [k])]
    if isinstance(value, list):
        return [u for i, v in enumerate(value) for u in _walk(v, name, parts + [i])]
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return [unit(f"{name}#{_pointer(parts) or '/'}", "value", shown)]


def _structured(text: str, name: str, fmt: str) -> tuple[list[dict], list[dict]]:
    try:
        data = json.loads(text) if fmt == "json" else yamlio.loads(text)
    except (ValueError, yamlio.DataError) as exc:
        return [], [unreadable(f"{name}#/", "parse_error", f"{fmt}として解析できない: {str(exc).splitlines()[0]}")]
    return _walk(data, name, []), []


def parse(path: Path, fmt: str) -> tuple[list[dict], list[dict]]:
    """(units, unreadable) を返す。name は保存したファイル名。"""
    name, data = path.name, path.read_bytes()
    if fmt in ("pdf", "docx", "xml", "skos"):
        from . import ingest_docs
        return ingest_docs.parse(data, name, fmt)
    text, bad = decode(data, name)
    if text is None:
        return [], bad
    if fmt in ("text", "markdown"):
        return _lines(text, name, fmt == "markdown"), []
    if fmt == "csv":
        return _csv(text, name)
    return _structured(text, name, fmt)


def build(run_id: str, input_id: str, sha256: str, path: Path, fmt: str) -> dict:
    units, bad = parse(path, fmt)
    if not units and not bad:
        bad = [unreadable(f"{path.name}#L1", "other", "読み取れる内容がない（空の入力）")]
    doc = {"schema_version": "1.0", "run_id": run_id, "input_id": input_id, "sha256": sha256, "format": fmt,
           "units": units, "unreadable": bad}
    errors = yamlio.schema_errors(doc, "ingest.schema.json")
    if errors:
        raise ValueError(f"取込み結果がスキーマに合わない: {errors[:3]}")
    return doc
