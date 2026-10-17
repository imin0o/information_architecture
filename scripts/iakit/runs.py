"""実行記録・入力登録簿の整合性検査（4.4、FR-08、IN-05、NFR-05、AC-11、AC-12）。"""

from pathlib import Path

from . import manifest
from .decisions import RANK, latest_active, max_classification

ROUTES = {
    "web_ia": ["analyze", "classify", "structure", "validate"],
    "taxonomy": ["analyze", "classify", "validate"],
}
MAX_REFINE_ROUNDS = 2


def input_errors(doc: dict) -> list[str]:
    errors, seen = [], set()
    for i in doc["inputs"]:
        if i["input_id"] in seen:
            errors.append(f"{i['input_id']}: input_id が重複している")
        seen.add(i["input_id"])
        if i["classification_source"] == "default" and i["classification"] != "internal":
            errors.append(f"{i['input_id']}: 区分未指定の既定値は internal（引き下げにはユーザーの指定が必要）")
    return errors


def input_file_results(doc: dict, base: Path) -> tuple[list[str], dict]:
    """登録した入力の実体を照合する。手元にない入力は not_run と数え、照合済みにしない。"""
    errors, counts = [], {"run": 0, "not_run": 0}
    for i in doc["inputs"]:
        path = base / i["path"]
        if not path.is_file():
            counts["not_run"] += 1
            continue
        counts["run"] += 1
        if manifest.sha256_file(path) != i["sha256"]:
            errors.append(f"{i['input_id']}: 入力の実体が登録時のハッシュと異なる（再登録し、依存する成果物をstaleにする）")
    return errors, counts


def _route_errors(run: dict) -> list[str]:
    route, expected = run["route"], ROUTES.get(run["task_type"])
    if expected is None:
        if route["supported"] or not route["unsupported_note"]:
            return [f"{run['task_type']}: MVP未対応の経路は supported: false と対応範囲の説明を記録する"]
        if run["status"] == "completed":
            return [f"{run['task_type']}: 未対応経路を completed にしない"]
        return []
    if not route["supported"] or route["stages"] != expected:
        return [f"{run['task_type']}: 標準経路 {expected} と一致しない（段階を黙って省略しない）"]
    return []


def _refine_errors(run: dict) -> list[str]:
    refine, errors = run["refine"], []
    if refine["rounds"] > MAX_REFINE_ROUNDS:
        errors.append(f"refine: 修正・再評価は{MAX_REFINE_ROUNDS}巡まで（{refine['rounds']}巡）")
    if refine["unresolved_critical"]:
        if run["status"] == "completed":
            errors.append("refine: 未解決の重大な指摘が残るのに completed になっている")
        if refine["rounds"] >= MAX_REFINE_ROUNDS and not (refine["reported_to_user"] and run["status"] == "unresolved"):
            errors.append("refine: 2巡後に残る重大な指摘は unresolved としてユーザーへ報告する")
    return errors


def _transmission_errors(run: dict, inputs: dict[str, dict]) -> list[str]:
    errors = []
    approvals = {a["approval_id"]: a for a in run["approvals"]}
    if len(approvals) != len(run["approvals"]):
        errors.append("approvals: approval_id が重複している")
    for a in run["approvals"]:
        missing = sorted(set(a["input_ids"]) - inputs.keys())
        if missing:
            errors.append(f"許可 {a['approval_id']}: 未登録の入力 {missing}")
    for n, t in enumerate(run["transmissions"]):
        tag = f"transmissions[{n}] {t['destination']}"
        missing = [i for i in t["input_ids"] if i not in inputs]
        if missing:
            errors.append(f"{tag}: 未登録の入力 {missing}")
        level = max_classification(inputs[i]["classification"] for i in t["input_ids"] if i in inputs)
        if t["outcome"] == "blocked":
            continue
        if t["destination_kind"] == "external_service" and level == "confidential":
            errors.append(f"{tag}: confidential の派生物を外部サービスへ送っている")
        a = approvals.get(t["approval_id"])
        if a is None:
            reason = "（資料内の命令は許可にならない）" if t.get("requested_by_input") else ""
            errors.append(f"{tag}: 許可記録のない送信{reason}。送信前に止める")
            continue
        if (a["destination"], a["destination_kind"]) != (t["destination"], t["destination_kind"]):
            errors.append(f"{tag}: 許可 {a['approval_id']} の送信先と一致しない")
        if t["data_kind"] not in a["data_kinds"]:
            errors.append(f"{tag}: {t['data_kind']} は許可 {a['approval_id']} の対象外")
        outside = sorted(set(t["input_ids"]) - set(a["input_ids"]))
        if outside:
            errors.append(f"{tag}: 入力 {outside} は許可 {a['approval_id']} の対象外")
        if RANK[level] > RANK[a["max_classification"]]:
            errors.append(f"{tag}: 区分 {level} が許可 {a['approval_id']} の上限 {a['max_classification']} を超える")
    return errors


def _artifact_errors(run: dict, inputs: dict[str, dict], actual: dict[str, str], active: set) -> list[str]:
    """actual は成果物ファイルの実ハッシュ（手元にあるものだけ）。active は現行の判断版。"""
    errors = []
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    if len(arts) != len(run["artifacts"]):
        errors.append("artifacts: artifact_id が重複している")
    for a in run["artifacts"]:
        tag = f"{a['artifact_id']} v{a['version']}"
        if a["artifact_id"] not in actual:
            errors.append(f"{tag}: 成果物ファイル {a['path']} がない")
        elif actual[a["artifact_id"]] != a["sha256"]:
            errors.append(f"{tag}: ファイルのハッシュが実行記録と一致しない")
        levels = []
        for dep in a["depends_on"]:
            ref = dep["ref"]
            current = inputs.get(ref, {}).get("sha256") if ref.startswith("INPUT") else arts.get(ref, {}).get("sha256")
            if current is None:
                errors.append(f"{tag}: 依存 {ref} が登録されていない")
                continue
            levels.append((inputs.get(ref) or arts[ref])["classification"])
            if a["status"] == "current" and current != dep["sha256"]:
                errors.append(f"{tag}: 依存 {ref} が変わったのに current のまま（staleにする）")
            if a["status"] == "current" and arts.get(ref, {}).get("status") == "stale":
                errors.append(f"{tag}: stale の {ref} に依存しているのに current のまま")
        if RANK[a["classification"]] < RANK[max_classification(levels)] and "classification_override" not in a:
            errors.append(f"{tag}: 機密区分が依存元より低い（引き下げにはユーザーの指定が必要）")
        for ref in a["decision_refs"]:
            key = (ref["decision_id"], ref["decision_revision"])
            if a["status"] == "current" and key not in active:
                errors.append(f"{tag}: 現行でない判断 {key[0]} r{key[1]} を参照している")
    return errors


def run_errors(run: dict, inputs_doc: dict, inputs_sha256: str | None, actual: dict[str, str],
               decisions: list[dict]) -> list[str]:
    inputs ={i["input_id"]: i for i in inputs_doc["inputs"]}
    errors = []
    if inputs_doc["run_id"] != run["run_id"]:
        errors.append("inputs.yaml の run_id が実行記録と一致しない")
    if inputs_sha256 and inputs_sha256 != run["inputs_sha256"]:
        errors.append("inputs.yaml が変わったのに実行記録の inputs_sha256 が更新されていない")
    known = {(d["decision_id"], d["decision_revision"]) for d in decisions}
    for ref in run["decision_refs"]:
        if (ref["decision_id"], ref["decision_revision"]) not in known:
            errors.append(f"decision_refs: {ref['decision_id']} r{ref['decision_revision']} が判断記録にない")
    errors += _route_errors(run) + _refine_errors(run) + _transmission_errors(run, inputs)
    errors += _artifact_errors(run, inputs, actual, latest_active(decisions))
    if run["status"] == "completed" and any(a["status"] == "stale" for a in run["artifacts"]):
        errors.append("stale の成果物が残るのに completed になっている")
    return errors
