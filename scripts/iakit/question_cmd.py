"""質問への回答の登録と、その仮定を使った判断・成果物の一覧（P1-3、IN-04）。"""

from pathlib import Path

from . import run_inputs, yamlio
from .run_cmd import load
from .run_outputs import DECISION_REF


def _affected(run: dict, decisions: list[dict], refs: set[tuple[str, int]]) -> tuple[list[str], list[str]]:
    """質問の判断を置換関係で現行版までたどり、旧版の成果物とその後続も挙げる。"""
    known = {(d["decision_id"], d["decision_revision"]): d for d in decisions}
    hit, related = [], set()
    for key, decision in known.items():
        if decision["status"] != "active":
            continue
        lineage, current = {key}, decision
        while current["supersedes"]:
            ref = current["supersedes"]
            previous = (ref["decision_id"], ref["decision_revision"])
            old = known.get(previous)
            if previous in lineage or old is None or old["status"] != "superseded":
                break
            lineage.add(previous)
            current = old
        if refs & lineage:
            hit.append(key)
            related |= lineage
    hit.sort()
    arts = {a["artifact_id"] for a in run["artifacts"]
            if any((r["decision_id"], r["decision_revision"]) in related for r in a["decision_refs"])}
    changed = True
    while changed:
        more = {a["artifact_id"] for a in run["artifacts"] if any(d["ref"] in arts for d in a["depends_on"])}
        changed = bool(more - arts)
        arts |= more
    return [f"{d} r{r}" for d, r in hit], sorted(arts)


def answer(directory: Path, question_id: str, source: Path, classification: str | None = None) -> dict:
    """回答を hearing_answer として登録する。判断の置き換えはエージェントが iakit decision revise で行う。"""
    question = directory / "questions" / f"{question_id}.md"
    if not question.is_file():
        raise ValueError(f"{question.relative_to(directory).as_posix()} がない")
    refs = {(m.group(1), int(m.group(2))) for m in DECISION_REF.finditer(question.read_text(encoding="utf-8"))}
    added = run_inputs.add(directory, source, "hearing_answer", f"{question_id} への回答", classification=classification)
    run, decisions = load(directory, "run.yaml"), yamlio.load(directory / "decisions.yaml")["decisions"]
    hit, arts = _affected(run, decisions, refs)
    out = {"question_id": question_id, "input_id": added["input_id"], "path": added["path"],
           "classification": added["classification"], "decisions": hit, "artifacts": arts}
    if added.get("instruction_like_units"):
        out["instruction_like_units"] = added["instruction_like_units"]
    out["next"] = (f"回答を {added['input_id']} の観察として使い、影響する判断を iakit decision revise で置き換える。"
                   "判断の版が変わると、参照する成果物は stale になる") if hit else (
        f"{question_id} は現行の判断をIDと版（IA-nnn rN）で挙げていない。回答が影響する判断を確かめる")
    return out
