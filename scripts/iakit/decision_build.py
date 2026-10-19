"""判断の既定値の補完（P1-1、P1-2）。実行記録を読まない純粋な関数だけを置き、run_cmd との循環を避ける。"""

from .run_outputs import STAGE_KINDS
from .runs import STAGE_SKILLS

LIBRARY_VERSION = "0.1.0"
KIND_STAGE = {kind: stage for stage, kinds in STAGE_KINDS.items() for kind in kinds}
DEFAULTS = {"sources": [], "input_observations": [], "alternatives": [], "alternatives_not_applicable_reason": None,
            "assumptions": [], "counter_evidence": [], "validation_status": "not_tested", "validation_record_ids": []}


def next_id(decisions: list[dict]) -> str:
    numbers = [int(d["decision_id"].split("-")[1]) for d in decisions]
    return f"IA-{max(numbers, default=0) + 1:03d}"


def skill_version(run: dict, stage: str) -> str:
    """段階を担当するSkillの版。記録がなければオーケストレーターの版。"""
    versions = {s["name"]: s["version"] for s in run["skills"]}
    return versions.get(STAGE_SKILLS.get(stage, ""), versions.get("ia-orchestrator", LIBRARY_VERSION))


def no_impact(run: dict, decisions: list[dict], artifact: dict, note: str, at: str) -> dict:
    """stale の成果物を内容を変えずに再登録するときの、影響なしの確認の記録。"""
    stage = KIND_STAGE.get(artifact["kind"], "validate")
    deps = ", ".join(sorted(d["ref"] for d in artifact["depends_on"])) or "なし"
    return {
        "decision_id": next_id(decisions), "decision_revision": 1, "run_id": run["run_id"], "status": "active",
        "supersedes": None, "stage": stage, "target_ids": [artifact["artifact_id"]],
        "problem": f"依存（{deps}）の変更が {artifact['artifact_id']}（{artifact['path']}）の内容に影響するか",
        "selected_method": "内容を変えずに再登録する（影響なし）", "rationale": note,
        **DEFAULTS,
        "alternatives": [{"method": "成果物を作り直す", "rejected_because": "依存の変更が内容に影響しないと確認した"}],
        "confidence": "low", "confidence_basis": "入力の観察を伴わない影響の確認で、実ユーザー調査は非該当",
        "classification": artifact["classification"], "recorded_at": at,
        "skill_version": skill_version(run, stage), "library_version": LIBRARY_VERSION,
    }
