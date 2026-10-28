"""実行ディレクトリの作成と、使うSkillの版・内容ハッシュの記録（4.4、FR-08）。"""

import hashlib
from pathlib import Path

from . import distverify, manifest, runs, yamlio
from .paths import SKILLS
from .run_store import now, save

STAGE_SKILLS = runs.STAGE_SKILLS
LATER_ROUTES = {
    "corporate_document": ["analyze", "present", "validate"], "exhibition_panel": ["analyze", "present", "validate"],
    "content_model": ["analyze", "classify", "model", "validate"], "design_system": ["analyze", "classify", "model", "validate"],
    "rag_knowledge": ["analyze", "classify", "model", "validate"],
}
CONTRACT_VERSION = "1.0"
# 実行ディレクトリは入力の写しを持つ。置き場所を問わず git status に出さないため、作るときに中へ無視の規則を置く
GITIGNORE = "# iakit の実行記録（入力の写しを含む）。git に入れない。\n*\n"


def skill_item(name: str, skills_root: Path = SKILLS) -> dict | None:
    """Skillディレクトリ全体の内容ハッシュと、SKILL.md の metadata.version。"""
    base = skills_root / name
    if not (base / "SKILL.md").is_file():
        return None
    text = (base / "SKILL.md").read_text(encoding="utf-8")
    front = yamlio.loads(text.split("---", 2)[1]) if text.startswith("---") else {}
    contract = (front.get("metadata") or {}).get("contract_version")
    if contract != CONTRACT_VERSION:
        raise ValueError(f"{name}: 入出力契約の版 {contract!r} は非互換（必要: {CONTRACT_VERSION}）。Skillを更新してから開始する")
    digest = hashlib.sha256()
    # 配布先で Python が作るキャッシュは含めない（distverify と同じ扱い）。含めると実行のたびにハッシュが変わる
    for path in sorted(p for p in base.rglob("*")
                       if p.is_file() and not distverify.IGNORED_PARTS & set(p.relative_to(base).parts)):
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
    found = distverify.set_errors(skills_root, needed, require_all=False)
    if found:
        raise ValueError(f"配布物の検査に失敗した: {found[:5]}。配布物をディレクトリ単位で入れ替えてから開始する")
    note = None if supported else (
        f"MVP未対応の経路（{' → '.join(stages)}）。不足Skill: {missing}。"
        "対応できる範囲はAnalyzeまでの分析で、残る段階は未実施として報告する")
    directory.mkdir(parents=True, exist_ok=True)
    # 利用者が置いた .gitignore は上書きしない（記録を git に入れると決めた場合）
    if not (directory / ".gitignore").exists():
        (directory / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
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
