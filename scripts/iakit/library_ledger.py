"""引用台帳の集計（BR-04）と、抽出品質の代表箇所の確認状態（P-M1-03）。"""


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


def sample_status(docs: dict) -> tuple[list[str], dict]:
    """抽出ごとの確認状態。根拠を登録した抽出に計画がなければ未計画として示す。"""
    errors, status = [], {}
    for rv in docs["samples"]["reviews"]:
        eid, results = rv["extraction_id"], [i["result"] for i in rv["items"]]
        if eid in status:
            errors.append(f"sample-reviews.yaml: {eid} が重複している")
        if any(r is not None for r in results) and not (rv["checked_by"] and rv["checked_at"]):
            errors.append(f"sample-reviews.yaml: {eid} の確認者・確認日時がない")
        status[eid] = ("not_run" if None in results else "issue" if "issue" in results else "ok")
    for r in docs["provenance"]["records"]:
        status.setdefault(r["extracted_ref"]["extraction_id"], "not_planned")
    return errors, dict(sorted(status.items()))
