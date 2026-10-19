"""解析データのエクスポートの取込み（P4-1）。

エクスポートを research_record として登録し、構成比と結果を analytics で導いて observations.yaml に追記する。
期間・取得元・対象の成果物の版・検証する構成比の基準を記録に残し、基準に書いた構成比だけを検証の範囲とする。
"""

from datetime import date
from pathlib import Path

from . import analytics, manifest, run_inputs, yamlio
from .run_cmd import load, now

NAME = "observations.yaml"
DEFAULT_MIN_TOTAL = 100


def _spec(data: dict) -> dict:
    start, end = (date.fromisoformat(data[k]) for k in ("period_start", "period_end"))
    if start > end:
        raise ValueError("期間の開始が終了より後になっている")
    if end > date.today():
        raise ValueError("期間の終了が未来になっている")
    if not 0 <= data["threshold"] <= 1:
        raise ValueError("threshold は 0〜1 の構成比で指定する")
    return {"source": data["source"], "aspect": data["aspect"],
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "dimension": data["dimension"], "metric": data["metric"], "value": data["value"],
            "filters": dict(data.get("filters") or {}), "op": data["op"], "threshold": data["threshold"],
            "min_total": data.get("min_total", DEFAULT_MIN_TOTAL)}


def _input(directory: Path, source: Path, description: str, classification: str | None) -> tuple[str, list[str]]:
    """同じ内容の research_record が登録済みなら再利用する。返り値は入力IDと命令文の候補の位置。"""
    digest = manifest.sha256_file(source)
    for i in load(directory, "inputs.yaml")["inputs"]:
        if i["kind"] == "research_record" and i["sha256"] == digest:
            return i["input_id"], []
    out = run_inputs.add(directory, source, "research_record", description, "csv", classification)
    return out["input_id"], out["instruction_like_units"]


def import_export(directory: Path, source: Path, data: dict) -> dict:
    """エクスポートを登録し、構成比と結果を導いて調査記録を追加する。"""
    spec = _spec(data)
    if not source.is_file():
        raise ValueError(f"{source} がない")
    observed = analytics.observe(analytics.read_rows(source), spec)  # 登録前に読めることを確かめる
    if not observed["total"]:
        raise ValueError("条件に合う行の指標の合計が0で、構成比を求められない")
    run = load(directory, "run.yaml")
    target = next((a for a in run["artifacts"] if a["artifact_id"] == data["target"]), None)
    if target is None or target["status"] != "current":
        raise ValueError(f"対象 {data['target']} は現行の成果物ではない")
    input_id, flagged = _input(directory, source, data["description"], data.get("classification"))
    record = next(i for i in load(directory, "inputs.yaml")["inputs"] if i["input_id"] == input_id)
    path = directory / NAME
    doc = yamlio.load(path) if path.is_file() else {"schema_version": "1.0", "run_id": run["run_id"], "studies": []}
    numbers = [int(s["study_id"].split("-")[1]) for s in doc["studies"]]
    result = analytics.judge(spec, observed)
    op = "以上" if spec["op"] == "ge" else "以下"
    study = {"study_id": f"STUDY-{max(numbers, default=0) + 1:03d}", "method": "analytics", "conducted_at": now(),
             "participants": max(1, int(observed["total"])),
             "target": {"artifact_id": target["artifact_id"], "version": target["version"]},
             "result": result, "input_id": input_id,
             "locator": f"{Path(record['path']).name}#{spec['dimension']}={spec['value']}",
             "summary": (f"{spec['period']['start']}〜{spec['period']['end']} の {spec['metric']} のうち "
                         f"{spec['dimension']}={spec['value']} が {observed['share']:.1%}"
                         f"（基準 {spec['threshold']:.0%} {op}、母数 {observed['total']:g}）"),
             "classification": record["classification"], "analytics": {**spec, "observed": observed}}
    doc["studies"].append(study)
    errors = yamlio.schema_errors(doc, "observation.schema.json")
    if errors:
        raise ValueError(f"{NAME} に書き込まない: " + " / ".join(errors[:5]))
    path.write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    out = {"study_id": study["study_id"], "input_id": input_id, "result": result, "observed": observed,
           "next": f"判断を tested_* にする場合は validation_record_ids に {study['study_id']} を入れて改訂する。"
                   "解析データが検証するのは基準に書いた構成比だけで、それ以外の仮説の根拠にしない"}
    if flagged:
        out["instruction_like_units"] = flagged
        out["next"] = f"命令文の候補を iakit input review {input_id} で記録する。" + out["next"]
    return out
