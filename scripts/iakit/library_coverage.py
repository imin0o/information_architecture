"""18冊の統合状況（library/coverage.yaml）の検査と、読んだ範囲・統合済み範囲の導出。

読んだ範囲は生成記録の inputs と検索で表示した行、統合済み範囲は出典登録簿の根拠の行範囲から導く。
読んだ範囲の中にも未統合の話題があるため、両者を区別して出力する。
"""

from . import yamlio
from .paths import MANIFEST


def _merge(spans: list[tuple[int, int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def ranges(docs: dict) -> dict[str, dict]:
    """抽出IDごとの read（精読した範囲）・displayed（検索で表示した行数）・integrated（根拠の範囲）。"""
    out: dict[str, dict] = {}

    def slot(eid: str) -> dict:
        return out.setdefault(eid, {"read": [], "displayed_lines": 0, "integrated": []})

    for g in docs["generations"]["generations"]:
        for i in g["inputs"]:
            slot(i["extraction_id"])["read"].append((i["start_line"], i["end_line"]))
        for s in g["searches"]:
            slot(s["extraction_id"])["displayed_lines"] += len(s["lines"])
    for r in docs["provenance"]["records"]:
        ref = r["extracted_ref"]
        slot(ref["extraction_id"])["integrated"].append((ref["start_line"], ref["end_line"]))
    return {eid: {"read": _merge(v["read"]), "displayed_lines": v["displayed_lines"],
                  "integrated": _merge(v["integrated"])} for eid, v in sorted(out.items())}


def errors_and_report(docs: dict) -> tuple[list[str], dict]:
    errors: list[str] = []
    books = {b["book_id"]: b for b in yamlio.load(MANIFEST)["books"]}
    listed: dict[str, dict] = {}
    for b in docs["coverage"]["books"]:
        if b["book_id"] in listed:
            errors.append(f"coverage.yaml: {b['book_id']} が重複している")
        listed[b["book_id"]] = b
    errors += [f"coverage.yaml: 登録簿の {bid} がない" for bid in sorted(set(books) - set(listed))]
    errors += [f"coverage.yaml: 登録簿にない {bid}" for bid in sorted(set(listed) - set(books))]
    integrated = {r["source_id"] for r in docs["provenance"]["records"]}
    for bid, b in sorted(listed.items()):
        if bid not in books:
            continue
        received = books[bid]["original"]["received"]
        if (b["status"] == "not_received") == received:
            errors.append(f"{bid}: status と登録簿の受領状態が一致しない")
        if b["status"] == "partial" and bid not in integrated:
            errors.append(f"{bid}: partial なのに登録済みの根拠がない")
        if b["status"] in ("not_received", "not_started") and bid in integrated:
            errors.append(f"{bid}: 根拠を登録済みなのに {b['status']} になっている")
    counts: dict[str, int] = {}
    for b in listed.values():
        counts[b["status"]] = counts.get(b["status"], 0) + 1
    return errors, {"status_counts": dict(sorted(counts.items())), "extractions": ranges(docs)}
