"""判断の追加・改訂・撤回（P1-2、FR-07）。

導出できる値（ID・版・記録日時・Skillとライブラリの版・照合状態・confidence・機密区分）はコマンドが補う。
書き込んだ後に契約検査を行い、書き込む前になかった誤りが出れば元に戻して拒否する。
"""

from collections import Counter
from copy import deepcopy
from pathlib import Path

from . import contracts, decision_build, provenance, validation_plan, yamlio
from .decisions import derive_confidence, max_classification, same_design
from .run_cmd import load, now, refresh, refresh_validation, save

AUTO = ("decision_id", "decision_revision", "run_id", "status", "supersedes", "recorded_at")


def _state(directory: Path, pending_artifacts: frozenset[str] = frozenset()) -> tuple[list[str], dict, dict, dict]:
    """契約検査の誤り一覧と、補完に使う照合状態・入力・調査記録（模擬評価の記録を含む）。"""
    docs, errors = contracts.load_bundle(directory)
    states = {}
    if not errors:
        found, derived = contracts.consistency_errors(directory, docs, provenance.registered_sources(),
                                                      pending_artifacts=pending_artifacts)
        errors, states = errors + found, derived.get("states", {})
    inputs = {i["input_id"]: i for i in docs.get("inputs.yaml", {"inputs": []})["inputs"]}
    studies = {s["study_id"]: s for s in docs.get("observations.yaml", {"studies": []})["studies"]}
    # 機密区分の導出では、模擬評価の記録も参照した記録として扱う
    studies.update((s["simulation_id"], s) for s in docs.get("simulations.yaml", {"simulations": []})["simulations"])
    return errors, states, inputs, studies


def _derive(d: dict, given: dict, states: dict, inputs: dict, studies: dict) -> dict:
    """照合状態・機密区分・confidence を記録から導く。指定があればその値を残し、検査で照合する。"""
    for s in d["sources"]:
        state = states.get((s.get("evidence_id"), s.get("claim_id")), {})
        for key in ("source_verification", "support_status"):
            if key not in s and key in state:
                s[key] = state[key]
    used = [inputs[o["input_id"]]["classification"] for o in d["input_observations"] if o.get("input_id") in inputs]
    used += [studies[sid]["classification"] for sid in d["validation_record_ids"] if sid in studies]
    if "classification" not in given:
        # 改訂時は旧版の区分を下限にする。区分の引き下げには明示的な指定が必要。
        prior = [d["classification"]] if "classification" in d else []
        d["classification"] = max_classification(used + prior or ["internal"])
    errors = yamlio.schema_errors({"schema_version": "1.0", "run_id": d["run_id"], "decisions": [d]},
                                  "decision.schema.json")
    if "confidence" not in given:
        d.pop("confidence", None)
        if not [e for e in errors if not e.startswith("decisions/0: required: confidence")]:
            d["confidence"] = derive_confidence(d, states)
    return d


def _commit(directory: Path, doc: dict, before: list[str], validation_revision: tuple[dict, dict] | None = None) -> list[str]:
    """判断を書き込み、依存をstaleにする。新しい誤りが出れば元に戻す。"""
    saved = {name: (directory / name).read_bytes() for name in ("decisions.yaml", "run.yaml")}
    if validation_revision:
        run = load(directory, "run.yaml")
        saved.update((a["path"], (directory / a["path"]).read_bytes())
                     for a in run["artifacts"] if a["status"] == "current")
        saved.update((p.relative_to(directory.resolve()).as_posix(), p.read_bytes())
                     for p in validation_plan.measurement_paths(directory, run) if p.is_file())
        if (directory / "simulations.yaml").is_file():
            saved["simulations.yaml"] = (directory / "simulations.yaml").read_bytes()
    try:
        save(directory, "decisions.yaml", doc)
        if validation_revision:
            refresh_validation(directory, *validation_revision)
        staled = refresh(directory)["staled"]
        # stale の後続の計画・調査対象は、再登録まで更新待ちにできる。
        # 通常の契約検査では猶予せず、完了前に必ず再確認する。
        pending = frozenset(a["artifact_id"] for a in load(directory, "run.yaml")["artifacts"] if a["status"] == "stale")
        added = Counter(_state(directory, pending_artifacts=pending)[0]) - Counter(before)
        if added:
            raise ValueError("契約検査に通らないため書き込まない: " + " / ".join(list(added)[:5]))
    except Exception:
        for name, data in saved.items():
            (directory / name).write_bytes(data)
        raise
    return staled


def _validation_only(previous: dict, revised: dict) -> bool:
    return (any(previous[k] != revised[k] for k in ("validation_status", "validation_record_ids"))
            and same_design(previous, revised))


def add(directory: Path, data: dict) -> dict:
    run, doc = load(directory, "run.yaml"), load(directory, "decisions.yaml")
    fixed = sorted(set(AUTO) & set(data))
    if fixed:
        raise ValueError(f"{fixed} はコマンドが決める。改訂は iakit decision revise を使う")
    before, states, inputs, studies = _state(directory)
    stage = data.get("stage", "")
    d = {"decision_id": decision_build.next_id(doc["decisions"]), "decision_revision": 1, "run_id": run["run_id"],
         "status": "active", "supersedes": None, **decision_build.DEFAULTS, **data, "recorded_at": now(),
         "skill_version": data.get("skill_version") or decision_build.skill_version(run, stage),
         "library_version": data.get("library_version") or decision_build.LIBRARY_VERSION}
    doc["decisions"].append(_derive(d, data, states, inputs, studies))
    staled = _commit(directory, doc, before)
    return {"decision": f"{d['decision_id']} r1", "confidence": d.get("confidence"),
            "classification": d["classification"], "staled": staled}


def _latest(doc: dict, decision_id: str) -> dict:
    revs = [d for d in doc["decisions"] if d["decision_id"] == decision_id]
    if not revs:
        raise ValueError(f"{decision_id} は判断記録にない")
    latest = max(revs, key=lambda d: d["decision_revision"])
    if latest["status"] != "active":
        raise ValueError(f"{decision_id} r{latest['decision_revision']} は {latest['status']} で、改訂できない")
    return latest


def revise(directory: Path, decision_id: str, changes: dict) -> dict:
    """最新版を写して変更を当て、版を上げる。旧版は superseded にする。"""
    doc = load(directory, "decisions.yaml")
    fixed = sorted(set(AUTO) & set(changes))
    if fixed:
        raise ValueError(f"{fixed} はコマンドが決める")
    before, states, inputs, studies = _state(directory)
    old = _latest(doc, decision_id)
    # confidence は導き直し、機密区分は旧版を下限にして継承する。
    d = {k: v for k, v in deepcopy(old).items() if k != "confidence"}
    for s in d["sources"]:
        s.pop("source_verification", None)
        s.pop("support_status", None)
    d.update(changes, decision_revision=old["decision_revision"] + 1, status="active", recorded_at=now(),
             supersedes={"decision_id": decision_id, "decision_revision": old["decision_revision"]})
    old["status"] = "superseded"
    d = _derive(d, changes, states, inputs, studies)
    doc["decisions"].append(d)
    validation_revision = (old, d) if _validation_only(old, d) else None
    staled = _commit(directory, doc, before, validation_revision)
    return {"decision": f"{decision_id} r{d['decision_revision']}", "superseded": f"{decision_id} r{old['decision_revision']}",
            "confidence": d.get("confidence"), "classification": d["classification"], "staled": staled}


def withdraw(directory: Path, decision_id: str) -> dict:
    doc = load(directory, "decisions.yaml")
    before = _state(directory)[0]
    latest = _latest(doc, decision_id)
    latest["status"] = "withdrawn"
    staled = _commit(directory, doc, before)
    return {"withdrawn": f"{decision_id} r{latest['decision_revision']}", "staled": staled}
