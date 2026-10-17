"""判断記録の整合性検査（FR-07、AC-04、AC-07）。

source_verification・support_status・confidence・validation_status は記録から導出し、
判断に書かれた値が導出値と異なれば拒否する。実行時の自己申告で状態を引き上げさせない。
"""

RANK = {"public": 0, "internal": 1, "confidential": 2}
TESTED = {"supported": "tested_supported", "refuted": "tested_refuted"}


def max_classification(values) -> str:
    return max(values, key=RANK.__getitem__, default="public")


def supersession_errors(decisions: list[dict]) -> list[str]:
    """判断IDごとに版が1から連続し、旧版だけが superseded で、supersedes が直前版を指す。"""
    errors: list[str] = []
    groups: dict[str, list[dict]] = {}
    for d in decisions:
        groups.setdefault(d["decision_id"], []).append(d)
    for did, revs in groups.items():
        revs.sort(key=lambda d: d["decision_revision"])
        numbers = [d["decision_revision"] for d in revs]
        if numbers != list(range(1, len(revs) + 1)):
            errors.append(f"{did}: 版が1からの連番ではない {numbers}（旧版を削除しない）")
            continue
        for d in revs[1:]:
            if d["supersedes"] != {"decision_id": did, "decision_revision": d["decision_revision"] - 1}:
                errors.append(f"{did} r{d['decision_revision']}: supersedes が直前版を指していない")
        for d in revs[:-1]:
            if d["status"] != "superseded":
                errors.append(f"{did} r{d['decision_revision']}: 新しい版があるのに status が {d['status']}")
        if revs[-1]["status"] == "superseded":
            errors.append(f"{did}: 最新版 r{revs[-1]['decision_revision']} が superseded になっている")
    return errors


def latest_active(decisions: list[dict]) -> set[tuple[str, int]]:
    return {(d["decision_id"], d["decision_revision"]) for d in decisions if d["status"] == "active"}


def derive_validation(study_results: list[str]) -> str:
    if not study_results:
        return "not_tested"
    if len(set(study_results)) == 1 and study_results[0] in TESTED:
        return TESTED[study_results[0]]
    return "tested_inconclusive"


def derive_confidence(d: dict, states: dict) -> str:
    """FR-07の判定。low の条件を優先し、次に high、それ以外を medium とする。"""
    primary_ok = all(states.get((s["evidence_id"], s["claim_id"])) == {
        "source_verification": "verified", "support_status": "supported"} for s in d["sources"] if s["primary"])
    if (not primary_ok or any(a["critical"] for a in d["assumptions"])
            or any(not c["resolved"] for c in d["counter_evidence"])
            or d["validation_status"] in ("tested_refuted", "tested_inconclusive")
            or not d["input_observations"]):
        return "low"
    return "high" if d["validation_status"] == "tested_supported" else "medium"


def study_index(studies: list[dict], inputs: dict[str, dict]) -> tuple[dict, list[str]]:
    """登録元と機密区分を検査し、無効・重複した調査を状態導出から除く。"""
    indexed, errors, invalid = {}, [], set()
    for study in studies:
        sid = study["study_id"]
        if sid in indexed:
            errors.append(f"{sid}: study_id が重複している")
            invalid.add(sid)
        indexed[sid] = study
        source = inputs.get(study["input_id"])
        if source is None:
            errors.append(f"{sid}: 調査の入力 {study['input_id']} が登録されていない")
            invalid.add(sid)
        elif source["kind"] != "research_record":
            errors.append(f"{sid}: 入力 {study['input_id']} は research_record ではない")
            invalid.add(sid)
        elif RANK[study["classification"]] < RANK[source["classification"]]:
            errors.append(f"{sid}: 調査の機密区分が入力 {study['input_id']} より低い")
            invalid.add(sid)
    return {sid: study for sid, study in indexed.items() if sid not in invalid}, errors


def _source_errors(tag: str, d: dict, claims: dict, states: dict) -> list[str]:
    errors = []
    counter = {c["ref"] for c in d["counter_evidence"]}
    for s in d["sources"]:
        cid, reg = s["claim_id"], claims.get(s["claim_id"])
        if reg is None or (reg["evidence_id"], reg["source_id"]) != (s["evidence_id"], s["source_id"]):
            errors.append(f"{tag}: {s['source_id']}・{s['evidence_id']}・{cid} の組が登録簿にない")
            continue
        if s["claim"] != reg["claim"]:
            errors.append(f"{tag}: {cid} の主張が登録と異なる（言い換えは新規レビューを要する）")
        state = states[(s["evidence_id"], cid)]
        for key in ("source_verification", "support_status"):
            if s[key] != state[key]:
                errors.append(f"{tag}: {cid} の {key} が {s[key]} だが記録からの導出は {state[key]}（自己申告は不可）")
        if state["support_status"] == "not_supported":
            if s["primary"]:
                errors.append(f"{tag}: not_supported の {cid} を採用理由（primary）に使っている")
            if cid not in counter:
                errors.append(f"{tag}: not_supported の {cid} を counter_evidence に挙げていない")
    return errors


def decision_errors(doc: dict, claims: dict, states: dict, inputs: dict[str, dict], studies: dict[str, dict],
                    artifact_versions: dict[str, int], target_ids: set[str]) -> list[str]:
    """claims は登録簿の主張索引、states は provenance.derive の導出値、artifact_versions は現行版。"""
    errors = supersession_errors(doc["decisions"])
    for d in doc["decisions"]:
        if d["run_id"] != doc["run_id"]:
            errors.append(f"{d['decision_id']} r{d['decision_revision']}: run_id が判断記録のファイルと一致しない")
    # 旧版は記録時点の状態を残すため、現在の登録簿・成果物の版とは照合しない
    for d in (d for d in doc["decisions"] if d["status"] != "superseded"):
        tag = f"{d['decision_id']} r{d['decision_revision']}"
        errors += [f"{tag}: 対象 {target} が登録されていない" for target in d["target_ids"] if target not in target_ids]
        errors += _source_errors(tag, d, claims, states)
        used = []
        for o in d["input_observations"]:
            if o["input_id"] not in inputs:
                errors.append(f"{tag}: {o['observation_id']} の入力 {o['input_id']} が登録されていない")
            else:
                used.append(inputs[o["input_id"]]["classification"])
        results = []
        for sid in d["validation_record_ids"]:
            study = studies.get(sid)
            if study is None:
                errors.append(f"{tag}: 調査記録 {sid} がない")
                continue
            used.append(study["classification"])
            results.append(study["result"])
            target = study["target"]
            if artifact_versions.get(target["artifact_id"]) != target["version"]:
                errors.append(f"{tag}: {sid} は {target['artifact_id']} v{target['version']} の調査で、現行版に適用できるか未確認")
        if d["validation_status"].startswith("tested_"):
            expected = derive_validation(results)
            if not results:
                errors.append(f"{tag}: {d['validation_status']} には実ユーザー調査の記録への参照が必要")
            elif d["validation_status"] != expected:
                errors.append(f"{tag}: validation_status が {d['validation_status']} だが調査結果からの導出は {expected}")
        elif d["validation_record_ids"]:
            errors.append(f"{tag}: 調査記録を参照しながら {d['validation_status']} になっている")
        if RANK[d["classification"]] < RANK[max_classification(used)]:
            errors.append(f"{tag}: 機密区分 {d['classification']} が使用した入力・調査記録の区分より低い")
        confidence = derive_confidence(d, states)
        if d["confidence"] != confidence:
            errors.append(f"{tag}: confidence が {d['confidence']} だが判定基準からの導出は {confidence}")
    return errors
