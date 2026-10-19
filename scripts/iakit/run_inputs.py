"""入力の登録・差し替え、履歴の追加、再開時の照合（IN-02、IN-05、IN-06、FR-08、AC-12）。"""

import shutil
from pathlib import Path

from . import ingest, manifest, runs, yamlio
from .decision_build import LIBRARY_VERSION
from .paths import ROOT
from .run_cmd import load, now, refresh, save

EVENTS_STATUS = {"interrupted": "interrupted", "resumed": "in_progress", "rerun": "in_progress",
                 "refine": "in_progress", "completed": "completed"}


def _store(directory: Path, source: Path, name: str | None = None) -> str:
    """入力の実体を実行ディレクトリの inputs/ へ固定する。既にディレクトリ内にあればそのまま使う。"""
    source = source.resolve()
    try:
        return source.relative_to(directory.resolve()).as_posix()
    except ValueError:
        pass
    target = directory / "inputs" / (name or source.name)
    if target.exists() and manifest.sha256_file(target) != manifest.sha256_file(source):
        raise ValueError(f"{target.name} は別の内容で登録済み。--name で別名を付ける")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target.relative_to(directory).as_posix()


def _ingest(directory: Path, run_id: str, record: dict) -> None:
    doc = ingest.build(run_id, record["input_id"], record["sha256"], directory / record["path"], record["format"])
    out = directory / "ingest" / f"{record['input_id']}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    record["ingest_sha256"] = manifest.sha256_file(out)
    record["unreadable"] = doc["unreadable"]


def add(directory: Path, source: Path, kind: str, description: str, fmt: str | None = None,
        classification: str | None = None, replace: str | None = None, name: str | None = None) -> dict:
    """入力を登録する。replace を指定すると既存の入力IDの実体を差し替え、依存する成果物を stale にする。"""
    if not source.is_file():
        raise ValueError(f"{source} がない")
    inputs = load(directory, "inputs.yaml")
    rel = _store(directory, source, name)
    record = {
        "input_id": replace or f"INPUT-{len(inputs['inputs']) + 1:03d}", "kind": kind,
        "format": fmt or ingest.guess_format(source), "description": description, "received_at": now(),
        "path": rel, "sha256": manifest.sha256_file(directory / rel),
        "classification": classification or "internal",
        "classification_source": "user" if classification else "default", "unreadable": [],
    }
    old = next((i for i in inputs["inputs"] if i["input_id"] == replace), None)
    if replace and old is None:
        raise ValueError(f"{replace} は登録されていない")
    if old and not classification:
        # 差し替えでも区分は引き下げない。指定がなければ旧登録の区分を引き継ぐ
        record.update(classification=old["classification"], classification_source=old["classification_source"])
    _ingest(directory, inputs["run_id"], record)
    if old:
        inputs["inputs"][inputs["inputs"].index(old)] = record
    else:
        inputs["inputs"].append(record)
    save(directory, "inputs.yaml", inputs)
    changed = old and any(old.get(key) != record.get(key) for key in ("sha256", "path", "format", "ingest_sha256"))
    if changed:
        # 同じ入力ID・位置でも内容が変われば確認し直す。送信の履歴は証跡として残す。
        run = load(directory, "run.yaml")
        if "input_reviews" in run:
            run["input_reviews"] = [r for r in run["input_reviews"] if r["input_id"] != replace]
            save(directory, "run.yaml", run)
    result = refresh(directory, changed_inputs=(record["input_id"],) if changed else ())
    flagged = _flagged(directory, record["input_id"])
    out = {"input_id": record["input_id"], "path": rel, "format": record["format"],
           "classification": record["classification"], "unreadable": len(record["unreadable"]),
           "instruction_like_units": flagged, "staled": result["staled"]}
    if flagged:  # 確認は登録の一部。登録後に止める依頼でも、記録してから止める（P-M6-02）
        out["next"] = (f"命令文の候補を確かめ、iakit input review {record['input_id']} で記録する。"
                       "記録のない候補が残る実行は契約検査に通らない")
    return out


def _flagged(directory: Path, input_id: str) -> list[str]:
    inputs = load(directory, "inputs.yaml")
    inputs["inputs"] = [i for i in inputs["inputs"] if i["input_id"] == input_id]
    if not inputs["inputs"]:
        raise ValueError(f"{input_id} は登録されていない")
    docs, errors = ingest.load_registered(directory, inputs, require_all=True)
    if errors:
        raise ValueError(" / ".join(errors))
    doc = docs[input_id]
    return [u["locator"] for u in doc["units"] if "instruction_like" in u.get("flags", [])]


def review(directory: Path, input_id: str, outcome: str, locators: list[str] | None = None,
           destination: str | None = None, note: str = "") -> dict:
    """命令文の候補（instruction_like）を確かめた結果を記録する。資料内の命令には従わない（AC-11）。
    blocked_request は資料が送信を求めた場合で、送信せずに transmissions へ blocked として記録する。"""
    if outcome == "blocked_request" and not destination:
        raise ValueError("blocked_request には資料が求めた送信先を --destination で示す")
    if not (directory / "ingest" / f"{input_id}.yaml").is_file():
        raise ValueError(f"{input_id} の取込み結果がない")
    flagged = _flagged(directory, input_id)
    unknown = sorted(set(locators or []) - set(flagged))
    if unknown:
        raise ValueError(f"{unknown} は {input_id} の命令文の候補ではない（候補: {flagged}）")
    run = load(directory, "run.yaml")
    reviews = run.setdefault("input_reviews", [])
    done = {(r["input_id"], r["locator"]) for r in reviews}
    targets = [loc for loc in (locators or flagged) if (input_id, loc) not in done]
    for loc in targets:
        reviews.append({"input_id": input_id, "locator": loc, "outcome": outcome, "note": note})
    if outcome == "blocked_request" and targets:
        run["transmissions"].append({"destination": destination, "destination_kind": "external_service",
                                     "data_kind": "input", "input_ids": [input_id], "approval_id": None,
                                     "outcome": "blocked", "requested_by_input": True})
    save(directory, "run.yaml", run)
    left = sorted(set(flagged) - {r["locator"] for r in reviews if r["input_id"] == input_id})
    return {"input_id": input_id, "reviewed": targets, "outcome": outcome, "unreviewed": left}


def resume_errors(directory: Path) -> list[str]:
    """中断後の再開前に、入力登録簿・入力の実体・取込み結果・成果物のハッシュを照合する。"""
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")
    errors = []
    if manifest.sha256_file(directory / "inputs.yaml") != run["inputs_sha256"]:
        errors.append("inputs.yaml が実行記録の後に変わった。iakit run refresh で依存をstaleにしてから進める")
    errors += ingest.load_registered(directory, inputs, require_all=True)[1]
    for i in inputs["inputs"]:
        path = directory / i["path"]
        if not path.is_file():
            errors.append(f"{i['input_id']}: 実体 {i['path']} がない（照合済みに数えない）")
        elif manifest.sha256_file(path) != i["sha256"]:
            errors.append(f"{i['input_id']}: 実体が登録時と異なる。iakit input add --replace で再登録する")
    for a in run["artifacts"]:
        path = directory / a["path"]
        if not path.is_file() or manifest.sha256_file(path) != a["sha256"]:
            errors.append(f"{a['artifact_id']}: {a['path']} が登録後に変わった。iakit run artifact で再登録する")
    return errors


def event(directory: Path, name: str, note: str, status: str | None = None) -> dict:
    """履歴を追加する。status は既定の遷移と異なる場合（2巡後の unresolved など）だけ指定する。"""
    if name == "resumed":
        errors = resume_errors(directory)
        if errors:
            raise ValueError("再開前の照合に失敗: " + " / ".join(errors))
    run = load(directory, "run.yaml")
    if name == "refine" and run["refine"]["rounds"] >= runs.MAX_REFINE_ROUNDS:
        raise ValueError(f"refine: 修正・再評価は{runs.MAX_REFINE_ROUNDS}巡まで")
    run["history"].append({"event": name, "at": now(), "note": note})
    if status or name in EVENTS_STATUS:
        run["status"] = status or EVENTS_STATUS[name]
    if run["status"] == "unresolved":
        run["refine"]["reported_to_user"] = True
    if name == "refine":
        run["refine"]["rounds"] += 1
    save(directory, "run.yaml", run)
    return {"event": name, "status": run["status"], "refine_rounds": run["refine"]["rounds"]}


def knowledge(directory: Path, ids: list[str], index_path: Path = ROOT / "library/index.yaml") -> dict:
    """段階で読んだ知識本文を、索引のパスと内容ハッシュで実行記録に残す（NFR-02、FR-08）。"""
    entries = {e["knowledge_id"]: e for e in yamlio.load(index_path)["entries"]}
    unknown = [i for i in ids if i not in entries]
    if unknown:
        raise ValueError(f"索引にない知識 {unknown}")
    run = load(directory, "run.yaml")
    recorded = {k["name"]: k for k in run["knowledge"]}
    for kid in ids:
        recorded[kid] = {"name": kid, "version": LIBRARY_VERSION,
                         "sha256": manifest.sha256_file(index_path.parents[1] / entries[kid]["path"])}
    run["knowledge"] = [recorded[k] for k in sorted(recorded)]
    save(directory, "run.yaml", run)
    return {"knowledge": sorted(recorded)}
