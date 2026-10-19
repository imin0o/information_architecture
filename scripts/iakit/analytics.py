"""解析データの調査記録の導出と照合（P4-1）。

GA4・Search Console などのCSVエクスポートから、指定した次元の値の構成比を指標の列で求め、
基準と比べた結果（supported・refuted・inconclusive）を導く。記録は analytics_cmd が書き、
契約検査は登録済みの入力から再計算して照合する。結果は自己申告させない。
"""

import csv
from pathlib import Path


def read_rows(path: Path) -> list[dict]:
    """先頭のコメント行（#）と空行を飛ばし、最初の表だけを読む。GA4の複数表のエクスポートに対応する。"""
    block = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not block and (not line.strip() or line.startswith("#")):
            continue
        if not line.strip():
            break
        block.append(line)
    if not block:
        raise ValueError(f"{path.name}: 表が見つからない")
    return list(csv.DictReader(block))


def _number(text: str | None, where: str) -> float:
    try:
        return float((text or "").replace(",", "").strip().rstrip("%"))
    except ValueError:
        raise ValueError(f"{where}: 数値ではない") from None


def observe(rows: list[dict], spec: dict) -> dict:
    """filters で母集団を絞り、次元が空の行（合計行）を除いて、value の行の指標の割合を求める。"""
    columns = set(rows[0]) if rows else set()
    missing = sorted({spec["dimension"], spec["metric"], *spec["filters"]} - columns)
    if missing:
        raise ValueError(f"列 {missing} がない（列: {sorted(columns)}）")
    matched = total = 0.0
    for n, row in enumerate(rows, start=2):
        if not (row[spec["dimension"]] or "").strip():
            continue
        if any((row[c] or "").strip() != v for c, v in spec["filters"].items()):
            continue
        amount = _number(row[spec["metric"]], f"{n}行目の {spec['metric']}")
        total += amount
        if row[spec["dimension"]].strip() == spec["value"]:
            matched += amount
    share = round(matched / total, 4) if total else 0.0
    return {"matched": matched, "total": total, "share": share}


def judge(spec: dict, observed: dict) -> str:
    """母数が min_total 未満なら inconclusive。基準を満たせば supported、満たさなければ refuted。"""
    if not observed["total"] or observed["total"] < spec["min_total"]:
        return "inconclusive"
    ok = observed["share"] >= spec["threshold"] if spec["op"] == "ge" else observed["share"] <= spec["threshold"]
    return "supported" if ok else "refuted"


def study_errors(directory: Path, studies: list[dict], inputs: dict[str, dict]) -> list[str]:
    """解析データの調査記録を、登録済みの入力から再計算して照合する。"""
    errors = []
    for s in (s for s in studies if s["method"] == "analytics" and s["input_id"] in inputs):
        sid, spec, path = s["study_id"], s["analytics"], directory / inputs[s["input_id"]]["path"]
        try:
            observed = observe(read_rows(path), spec)
        except (ValueError, OSError) as exc:
            errors.append(f"{sid}: 入力 {s['input_id']} から再計算できない（{exc}）")
            continue
        if observed != spec["observed"]:
            errors.append(f"{sid}: 観測値が入力 {s['input_id']} からの再計算 {observed} と異なる")
        expected = judge(spec, observed)
        if s["result"] != expected:
            errors.append(f"{sid}: result が {s['result']} だが基準からの導出は {expected}（自己申告は不可）")
        if s["conducted_at"][:10] < spec["period"]["end"]:
            errors.append(f"{sid}: 期間の終了 {spec['period']['end']} より前に取得したことになっている")
    return errors
