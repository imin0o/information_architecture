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

from . import manifest, yamlio

FORMATS = {
    ".txt": "text", ".md": "markdown", ".markdown": "markdown", ".pdf": "pdf", ".docx": "docx", ".csv": "csv",
    ".xml": "xml", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".rdf": "skos", ".skos": "skos",
}
# 日本語は依頼・命令の形だけを拾う。名詞（「送信先」「アップロード画面」）と否定（「実行していない」）は候補にしない
INSTRUCTION = re.compile(
    r"((送信|転送|アップロード)(して(?!い)|せよ|しろ|すること|しなさい|を?お?願)"
    r"|(共有|実行|削除|無視|公開)して(?!い)|従って(?!い)"
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


def _csv(text: str, name: str, *, first_table: bool = False) -> tuple[list[dict], list[dict]]:
    """解析CSVと共通の読取り。コメント付きエクスポートは空行で表を区切り、元の行位置を保つ。"""
    reader = csv.reader(io.StringIO(text, newline=""))
    lines = text.splitlines()
    export = False
    header, units, bad, previous = None, [], [], 0
    for row in reader:
        n, previous = previous + 1, reader.line_num
        if not any(cell.strip() for cell in row):
            if header is not None and first_table:
                break
            if export:
                header = None
            continue
        if header is None:
            if lines[n - 1].startswith("#") and (len(row) == 1 or lines[n - 1].startswith("# ")):
                export = True
                units.append(unit(f"{name}#row={n}", "line", ",".join(row)))
                continue
            header = row
            units.append(unit(f"{name}#row={n}", "header", ",".join(row)))
            if len(header) != len(set(header)):
                bad.append(unreadable(f"{name}#row={n}", "parse_error", "列名が重複している。列を一意に識別できないためデータ行は取り込んでいない"))
                return units, bad
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
        data = json.loads(text, object_pairs_hook=_unique_object) if fmt == "json" else yamlio.loads(text)
    except (ValueError, yamlio.DataError) as exc:
        return [], [unreadable(f"{name}#/", "parse_error", f"{fmt}として解析できない: {str(exc).splitlines()[0]}")]
    return _walk(data, name, []), []


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("JSONのキーが重複している")
        data[key] = value
    return data


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


def load_registered(directory: Path, inputs_doc: dict, require_all: bool = False) -> tuple[dict, list[str]]:
    """内容ハッシュ・スキーマ・入力との対応を照合し、検査に通った取込み結果だけを返す。"""
    docs, errors = {}, []
    for record in inputs_doc["inputs"]:
        iid = record["input_id"]
        path = directory / "ingest" / f"{iid}.yaml"
        if not path.is_file():
            if require_all or "ingest_sha256" in record:
                errors.append(f"{iid}: 取込み結果がない。iakit input add --replace で再登録する")
            continue  # 手書きの旧契約素材には取込み結果がない場合がある
        try:
            doc = yamlio.load(path)
            found = yamlio.schema_errors(doc, "ingest.schema.json")
            if found:
                errors.extend(f"ingest/{path.name} {e}" for e in found[:3])
                continue
            if (doc["run_id"], doc["input_id"], doc["sha256"], doc["format"], doc["unreadable"]) != (
                    inputs_doc["run_id"], iid, record["sha256"], record["format"], record["unreadable"]):
                errors.append(f"{iid}: 取込み結果が現在の入力登録と対応しない。再登録する")
            elif record.get("ingest_sha256") != manifest.sha256_file(path):
                errors.append(f"{iid}: 取込み結果の内容ハッシュが未登録か一致しない。iakit input add --replace で再登録する")
            else:
                docs[iid] = doc
        except (OSError, ValueError):
            errors.append(f"{iid}: 取込み結果を読み込めない。iakit input add --replace で再登録する")
    return docs, errors
