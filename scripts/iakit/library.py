"""知識ライブラリの検査（iakit library check）。BR-02・BR-04。

索引・用語・矛盾・引用台帳・生成記録の参照整合性と、本文が引く主張の一致を確かめる。
採用状態は記録させず、主張レビューと照合記録から導出する（provenance.derive）。
adopted は、本文が引く全主張が原典照合済みかつ supported で、有効な照合記録の knowledge_files に
その本文が含まれる場合に限る。採用の単位は本文のうち「仮説・補完」節より前の部分（adoptable_text）。
"""

import re
from pathlib import Path

from . import library_coverage, manifest, provenance, snapshot, yamlio
from .paths import EXTRACTED, ROOT

FILES = {
    "coverage": ("library/coverage.yaml", "book-coverage.schema.json"),
    "index": ("library/index.yaml", "knowledge-index.schema.json"),
    "terms": ("library/terms.yaml", "terms.schema.json"),
    "conflicts": ("library/conflicts.yaml", "conflict.schema.json"),
    "quotes": ("library/quotes.yaml", "quote-ledger.schema.json"),
    "generations": ("library/build-logs/knowledge-generation.yaml", "knowledge-generation.schema.json"),
    "provenance": ("library/provenance.yaml", "provenance.schema.json"),
}
RECEIPTS = ("verification/receipts.yaml", "verification-receipt.schema.json")
BODY_DIRS = ("books", "concepts", "methods", "patterns")
CLAIM_REF = re.compile(r"\[(CLAIM-[0-9]{4})\]")
QUOTE_MAX_WORDS = 50
HYPOTHESIS_HEADING = "## 仮説・補完"


def adoptable_text(text: str) -> str:
    """採用の対象になる部分。配布生成でも同じ境界を使い、仮説・補完の節を採用済みの知識として同梱しない。"""
    lines = text.splitlines(keepends=True)
    cut = next((i for i, line in enumerate(lines) if line.rstrip() == HYPOTHESIS_HEADING), len(lines))
    return "".join(lines[:cut])


def _boundary_errors(rel: str, text: str) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    if HYPOTHESIS_HEADING not in lines:
        return []
    errors = []
    if lines.count(HYPOTHESIS_HEADING) > 1:
        errors.append(f"{rel}: 「仮説・補完」節が複数ある")
    after = lines[lines.index(HYPOTHESIS_HEADING) + 1:]
    if any(line.startswith(("# ", "## ")) for line in after):
        errors.append(f"{rel}: 「仮説・補完」節は本文の最後に置く（後ろの節が採用対象から外れる）")
    return errors


def _load(root: Path, rel: str, schema: str, errors: list[str], required: bool = True):
    path = root / rel
    if not path.is_file():
        if required:
            errors.append(f"{rel}: ファイルがない")
        return None
    try:
        data = yamlio.load(path)
    except (OSError, ValueError) as exc:
        errors.append(f"{rel}: 読込み失敗: {exc}")
        return None
    found = yamlio.schema_errors(data, schema)
    errors.extend(f"{rel} {e}" for e in found)
    return None if found else data


def _ids(items: list[dict], key: str, label: str, errors: list[str]) -> set[str]:
    seen: set[str] = set()
    for item in items:
        if item[key] in seen:
            errors.append(f"{label}: {item[key]} が重複している")
        seen.add(item[key])
    return seen


def _case_items(root: Path) -> tuple[set[str], set[str]]:
    cases = {yamlio.load(p)["case_id"] for p in (root / "tests/cases").glob("*/case.yaml")}
    rubric = {i["item"] for i in yamlio.load(root / "tests/cases/rubric.yaml")["items"]}
    return cases, rubric


def reference_errors(docs: dict, root: Path, sources: dict) -> list[str]:
    errors: list[str] = []
    claims = provenance.claims_index(docs["provenance"])
    terms = _ids(docs["terms"]["terms"], "term_id", "terms.yaml", errors)
    conflicts = _ids(docs["conflicts"]["conflicts"], "conflict_id", "conflicts.yaml", errors)
    entries = _ids(docs["index"]["entries"], "knowledge_id", "index.yaml", errors)
    _ids(docs["index"]["needs"], "need_id", "index.yaml", errors)
    _ids(docs["quotes"]["quotes"], "quote_id", "quotes.yaml", errors)
    _ids(docs["generations"]["generations"], "generation_id", "knowledge-generation.yaml", errors)

    def missing(where: str, refs, known, label: str) -> None:
        errors.extend(f"{where}: 未登録の{label} {r}" for r in refs if r not in known)

    for t in docs["terms"]["terms"]:
        missing(t["term_id"], t["claims"], claims, "主張")
    for c in docs["conflicts"]["conflicts"]:
        ids = [p["claim_id"] for p in c["positions"]]
        missing(c["conflict_id"], ids, claims, "主張")
        if len(set(ids)) < 2:
            errors.append(f"{c['conflict_id']}: 対立する主張が2件未満")
    registered = set()
    for e in docs["index"]["entries"]:
        kid, path = e["knowledge_id"], root / e["path"]
        registered.add(e["path"])
        missing(kid, e["claims"], claims, "主張")
        missing(kid, e["terms"], terms, "用語")
        missing(kid, e["conflicts"], conflicts, "矛盾記録")
        if path.suffix != ".md" or not path.is_file():
            errors.append(f"{kid}: 本文 {e['path']} がない")
            continue
        text = path.read_text(encoding="utf-8")
        errors += _boundary_errors(e["path"], text)
        if set(CLAIM_REF.findall(text)) != set(e["claims"]):
            errors.append(f"{kid}: 本文が引く主張と索引の claims が一致しない")
    for d in BODY_DIRS:
        for path in sorted((root / "library" / d).rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            if rel not in registered:
                errors.append(f"{rel}: 索引に登録されていない知識本文")
    cases, rubric = _case_items(root)
    for n in docs["index"]["needs"]:
        missing(n["need_id"], n["cases"], cases, "ケース")
        missing(n["need_id"], n["rubric_items"], rubric, "ルーブリック項目")
        missing(n["need_id"], n["entries"], entries, "知識")
        if n["gap"] is None and not n["entries"]:
            errors.append(f"{n['need_id']}: 対応する知識がないのに gap が null")
    for q in docs["quotes"]["quotes"]:
        qid, ref = q["quote_id"], q["extracted_ref"]
        if q["source_id"] not in sources:
            errors.append(f"{qid}: 未登録の source_id {q['source_id']}")
        if snapshot.ID_PATTERN.fullmatch(ref["extraction_id"])[1] != snapshot.source_tag(q["source_id"]):
            errors.append(f"{qid}: extraction_id が source_id の抽出ではない")
        if ref["start_line"] > ref["end_line"]:
            errors.append(f"{qid}: 行範囲が逆順")
        if len(q["text"].split()) > QUOTE_MAX_WORDS:
            errors.append(f"{qid}: 直接引用が{QUOTE_MAX_WORDS}語を超える")
        if not (root / q["used_in"]).is_file():
            errors.append(f"{qid}: 使用先 {q['used_in']} がない")
    for g in docs["generations"]["generations"]:
        if not (root / g["instruction"]["path"]).is_file():
            errors.append(f"{g['generation_id']}: 指示 {g['instruction']['path']} がない")
        elif manifest.sha256_file(root / g["instruction"]["path"]) != g["instruction"]["sha256"]:
            errors.append(f"{g['generation_id']}: 指示のハッシュが一致しない（指示を変えたら版を分けて記録する）")
    return errors


def ledger(docs: dict) -> dict:
    """文献ごとの件数・延べ語数・同一範囲の重複を除いた語数。部分的に重なる範囲は未判定として数える。"""
    rows: dict[str, list[tuple[str, tuple, int]]] = {}
    for r in docs["provenance"]["records"]:
        ref = r["extracted_ref"]
        rows.setdefault(r["source_id"], []).append(
            ("preview", (ref["extraction_id"], ref["start_line"], ref["end_line"]), len(r["excerpt_preview"].split())))
    for q in docs["quotes"]["quotes"]:
        ref = q["extracted_ref"]
        rows.setdefault(q["source_id"], []).append(
            (q["use"], (ref["extraction_id"], ref["start_line"], ref["end_line"]), len(q["text"].split())))
    out = {}
    for source, items in sorted(rows.items()):
        unique = {(kind == "preview", span): words for kind, span, words in items}
        spans = sorted({span for _, span, _ in items})
        overlaps = sum(1 for a, b in zip(spans, spans[1:]) if a[0] == b[0] and b[1] <= a[2])
        out[source] = {
            "previews": sum(1 for k, _, _ in items if k == "preview"),
            "quotes": sum(1 for k, _, _ in items if k != "preview"),
            "words_total": sum(w for _, _, w in items),
            "words_unique_spans": sum(unique.values()),
            "overlap_undetermined": overlaps,
        }
    return out


def adoption(docs: dict, receipts: dict, derived: dict) -> dict:
    """知識ごとの採用状態。not_supported の主張を引く本文は、採用理由に使えないことを示すため別に数える。"""
    owner = {cid: v["evidence_id"] for cid, v in provenance.claims_index(docs["provenance"]).items()}
    reviewed_files: dict[str, set[str]] = {}
    for rc in receipts["receipts"]:
        if rc["receipt_id"] not in derived["stale_receipts"]:
            for c in rc["claims"]:
                reviewed_files.setdefault(c["claim_id"], set()).update(f["path"] for f in rc["targets"]["knowledge_files"])
    result = {}
    for e in docs["index"]["entries"]:
        st = [derived["states"].get((owner.get(c), c), {}) for c in e["claims"]]
        if any(s.get("support_status") == "not_supported" for s in st):
            result[e["knowledge_id"]] = "not_supported"
            continue
        ok = st and all(s.get("source_verification") == "verified" and s.get("support_status") == "supported" for s in st)
        bound = all(e["path"] in reviewed_files.get(c, ()) for c in e["claims"])
        result[e["knowledge_id"]] = "adopted" if ok and bound else "hypothesis"
    return result


def check(root: Path = ROOT, extracted_root: Path = EXTRACTED) -> dict:
    errors: list[str] = []
    docs = {k: _load(root, rel, schema, errors) for k, (rel, schema) in FILES.items()}
    receipts = _load(root, *RECEIPTS, errors, required=False) or {"schema_version": "1.0", "receipts": []}
    report = {"check": "library", "result": "fail", "errors": errors}
    if errors:
        return report
    sources = provenance.registered_sources()
    errors += provenance.reference_errors(docs["provenance"], receipts, sources)
    errors += reference_errors(docs, root, sources)
    derived = provenance.derive(docs["provenance"], receipts, root, extracted_root)
    states = adoption(docs, receipts, derived)
    coverage_errors, coverage = library_coverage.errors_and_report(docs)
    errors += coverage_errors
    edited = sorted({o["path"] for g in docs["generations"]["generations"] for o in g["outputs"]
                     if not (root / o["path"]).is_file() or manifest.sha256_file(root / o["path"]) != o["sha256"]})
    needs = {n["need_id"]: ("gap" if not n["entries"] else
                            "adopted" if all(states.get(k) == "adopted" for k in n["entries"]) and n["gap"] is None
                            else "pending") for n in docs["index"]["needs"]}
    report.update({
        "result": "fail" if errors else "pass",
        "knowledge": {s: sorted(k for k, v in states.items() if v == s)
                      for s in ("adopted", "hypothesis", "not_supported")},
        "needs": needs,
        "coverage": coverage,
        "quote_ledger": ledger(docs),
        "edited_after_generation": edited,
        # 人による原典・主張の確認はこの検査で代替しない
        "human_review": "not_run",
    })
    return report
