"""実行ディレクトリの作成・入力登録・成果物登録・stale化・再開（4.4、FR-08、IN-02、AC-12）。

ハッシュ・版・依存・機密区分の継承は手で書かず、このコマンドで更新する。判断・承認・送信の記録は
エージェントが YAML に書き、iakit contracts check で検査する。
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from . import manifest, runs, yamlio
from .decisions import max_classification
from .paths import ROOT

SKILLS = ROOT / "skills"
STAGE_SKILLS = {"analyze": "ia-analysis", "classify": "ia-taxonomy", "structure": "ia-structure",
                "model": "ia-content-model", "present": "ia-presentation", "validate": "ia-evaluation"}
LATER_ROUTES = {
    "corporate_document": ["analyze", "present", "validate"], "exhibition_panel": ["analyze", "present", "validate"],
    "content_model": ["analyze", "classify", "model", "validate"], "design_system": ["analyze", "classify", "model", "validate"],
    "rag_knowledge": ["analyze", "classify", "model", "validate"],
}
YAML_KINDS = {"content_inventory", "controlled_vocabulary", "taxonomy", "sitemap", "review_findings"}
DECISION_REF = re.compile(r"\b(IA-[0-9]{3}) r([0-9]+)\b")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load(directory: Path, name: str) -> dict:
    path = directory / name
    if not path.is_file():
        raise ValueError(f"{path} がない。先に iakit run init を実行する")
    return yamlio.load(path)


def save(directory: Path, name: str, doc: dict) -> None:
    schema = {"run.yaml": "run.schema.json", "inputs.yaml": "input.schema.json",
              "decisions.yaml": "decision.schema.json"}[name]
    errors = yamlio.schema_errors(doc, schema)
    if errors:
        raise ValueError(f"{name} がスキーマに合わない: {errors[:3]}")
    (directory / name).write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")


def skill_item(name: str, skills_root: Path = SKILLS) -> dict | None:
    """Skillディレクトリ全体の内容ハッシュと、SKILL.md の metadata.version。"""
    base = skills_root / name
    if not (base / "SKILL.md").is_file():
        return None
    text = (base / "SKILL.md").read_text(encoding="utf-8")
    front = yamlio.loads(text.split("---", 2)[1]) if text.startswith("---") else {}
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        digest.update(path.relative_to(base).as_posix().encode() + b"\0" + manifest.sha256_file(path).encode() + b"\n")
    return {"name": name, "version": str((front.get("metadata") or {}).get("version", "0.0.0")),
            "sha256": digest.hexdigest()}


def init(directory: Path, run_id: str, task_type: str, host: str, model: str, host_version: str | None = None,
         model_version: str | None = None, skills_root: Path = SKILLS) -> dict:
    if (directory / "run.yaml").exists():
        raise ValueError(f"{directory} には実行記録がある。再開は iakit run resume を使う")
    supported = task_type in runs.ROUTES
    stages = runs.ROUTES.get(task_type) or LATER_ROUTES[task_type]
    needed = ["ia-orchestrator"] + [STAGE_SKILLS[s] for s in stages]
    items = {n: skill_item(n, skills_root) for n in needed}
    missing = [n for n, item in items.items() if item is None]
    if supported and missing:
        raise ValueError(f"標準経路に必要なSkillがない: {missing}。段階を省略せず、配置してから開始する")
    note = None if supported else (
        f"MVP未対応の経路（{' → '.join(stages)}）。不足Skill: {missing}。"
        "対応できる範囲はAnalyzeまでの分析で、残る段階は未実施として報告する")
    directory.mkdir(parents=True, exist_ok=True)
    save(directory, "inputs.yaml", {"schema_version": "1.0", "run_id": run_id, "inputs": []})
    save(directory, "decisions.yaml", {"schema_version": "1.0", "run_id": run_id, "decisions": []})
    run = {
        "schema_version": "1.0", "run_id": run_id, "task_type": task_type,
        "route": {"stages": stages, "supported": supported, "unsupported_note": note},
        "status": "in_progress", "inputs_sha256": manifest.sha256_file(directory / "inputs.yaml"),
        "environment": {"host": host, "host_version": host_version, "model": model, "model_version": model_version,
                        "settings": {}},
        "skills": [i for i in items.values() if i], "knowledge": [], "artifacts": [], "decision_refs": [],
        "approvals": [], "transmissions": [], "refine": {"rounds": 0, "unresolved_critical": [], "reported_to_user": False},
        "history": [{"event": "started", "at": now(), "note": f"{task_type}（supported: {supported}）"}],
    }
    save(directory, "run.yaml", run)
    return {"run_id": run_id, "stages": stages, "supported": supported, "missing_skills": missing}


def _current_hashes(run: dict, inputs: dict) -> dict[str, str]:
    found = {i["input_id"]: i["sha256"] for i in inputs["inputs"]}
    found.update({a["artifact_id"]: a["sha256"] for a in run["artifacts"]})
    return found


def refresh(directory: Path) -> dict:
    """inputs.yaml のハッシュと判断参照を更新し、依存が変わった成果物を連鎖的に stale にする。"""
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")
    decisions = load(directory, "decisions.yaml")["decisions"]
    sha = manifest.sha256_file(directory / "inputs.yaml")
    if sha != run["inputs_sha256"]:
        run["inputs_sha256"] = sha
        if run["artifacts"]:
            # 成果物を作り始めた後の入力変更だけを履歴に残す。開始時の登録は started に含める
            run["history"].append({"event": "input_changed", "at": now(), "note": "入力登録簿を更新した"})
    current, staled, changed = _current_hashes(run, inputs), [], True
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    while changed:
        changed = False
        for a in (a for a in run["artifacts"] if a["status"] == "current"):
            for dep in a["depends_on"]:
                ref = dep["ref"]
                if current.get(ref) != dep["sha256"] or arts.get(ref, {}).get("status") == "stale":
                    a.update(status="stale", stale_reason=f"依存 {ref} が変わった。再生成か影響なしの確認まで使わない")
                    staled.append(a["artifact_id"])
                    changed = True
                    break
    if staled and run["status"] == "completed":
        run["status"] = "in_progress"
    run["decision_refs"] = [{"decision_id": d["decision_id"], "decision_revision": d["decision_revision"]}
                            for d in decisions]
    if (directory / "findings.yaml").is_file():
        found = yamlio.load(directory / "findings.yaml")["findings"]
        run["refine"]["unresolved_critical"] = sorted(
            f["finding_id"] for f in found if f["severity"] == "critical" and f["status"] in ("open", "unresolved"))
    save(directory, "run.yaml", run)
    return {"inputs_sha256": sha, "staled": staled}


def _body_refs(kind: str, body, text: str) -> tuple[set[str], list[tuple[str, int]]]:
    """本体が参照する入力・成果物IDと判断参照。"""
    # 指摘の basis は評価した時点の判断の版を示す証跡で、現行の判断への参照として扱わない
    refs, decisions = set(), ([] if kind == "review_findings" else
                              [(m.group(1), int(m.group(2))) for m in DECISION_REF.finditer(text)])
    if kind == "content_inventory":
        refs.update(i["input_id"] for i in body["items"])
    elif kind == "controlled_vocabulary":
        refs.update(s["input_id"] for c in body["concepts"] for s in c["sources"])
    elif kind in ("taxonomy", "sitemap"):
        refs.add(body["inventory"]["artifact_id"])
        if kind == "taxonomy":
            refs.add(body["vocabulary"]["artifact_id"])
            decisions += [(s["decision_ref"]["decision_id"], s["decision_ref"]["decision_revision"])
                          for s in body["schemes"]]
    elif kind == "review_findings":
        refs.update(f["target"]["artifact_id"] for f in body["findings"])
    return refs, decisions


def artifact(directory: Path, rel: str, kind: str, depends: list[str], decision_refs: list[str]) -> dict:
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")
    path = directory / rel
    if not path.is_file():
        raise ValueError(f"{rel} がない")
    text = path.read_text(encoding="utf-8")
    body = yamlio.load(path) if kind in YAML_KINDS else None
    existing = next((a for a in run["artifacts"] if a["kind"] == kind), None)
    sha = manifest.sha256_file(path)
    if existing and existing["path"] != rel:
        raise ValueError(f"{kind} は {existing['path']} で登録済み。同じ型の成果物は1つにする")
    if existing:
        version = existing["version"] if existing["sha256"] == sha and existing["status"] == "current" else existing["version"] + 1
        aid = existing["artifact_id"]
    else:
        version = 1
        aid = f"ART-{len(run['artifacts']) + 1:03d}"
    refs, found = _body_refs(kind, body, text)
    if kind == "sitemap":
        refs.update(a["artifact_id"] for a in run["artifacts"] if a["kind"] == "taxonomy")
        if any(n["concept_id"] for n in body["nodes"]):
            refs.update(a["artifact_id"] for a in run["artifacts"] if a["kind"] == "controlled_vocabulary")
    refs.update(depends)
    if aid in refs:
        raise ValueError(f"{aid} は登録する成果物自身のIDで、依存にできない")
    current = _current_hashes(run, inputs)
    unknown = sorted(r for r in refs if r not in current)
    if unknown:
        raise ValueError(f"未登録の依存 {unknown}")
    levels = {i["input_id"]: i["classification"] for i in inputs["inputs"]}
    levels.update({a["artifact_id"]: a["classification"] for a in run["artifacts"]})
    level = max_classification(levels[r] for r in refs)
    if existing and "classification_override" in existing:
        level = existing["classification"]
    if body is not None and isinstance(body, dict) and "artifact_id" in body:
        expected = {"artifact_id": aid, "version": version, "classification": level}
        wrong = {k: v for k, v in expected.items() if body.get(k) != v}
        if wrong:
            raise ValueError(f"{rel}: 本体の {', '.join(f'{k} を {v}' for k, v in wrong.items())} にしてから登録する")
    explicit = [(r.split(":")[0], int(r.split(":")[1])) for r in decision_refs]
    entry = {
        "artifact_id": aid, "kind": kind, "path": rel, "version": version, "sha256": sha, "status": "current",
        "stale_reason": None, "classification": level,
        "depends_on": [{"ref": r, "sha256": current[r]} for r in sorted(refs)],
        "decision_refs": [{"decision_id": d, "decision_revision": r} for d, r in sorted(set(explicit + found))],
    }
    if existing and "classification_override" in existing:
        entry["classification_override"] = existing["classification_override"]
    if existing:
        run["artifacts"][run["artifacts"].index(existing)] = entry
    else:
        run["artifacts"].append(entry)
    save(directory, "run.yaml", run)
    result = refresh(directory)
    return {"artifact_id": aid, "version": version, "classification": level, "staled": result["staled"]}
