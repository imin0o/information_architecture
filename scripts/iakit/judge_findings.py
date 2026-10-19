"""独立判定の結果と findings.yaml の指摘の対応（P3-3）。

fail の判定の問題と fail の項目を指摘として加え、Refineへ戻す（上限に達していれば未解決で終える）。
判定を受け直したときは、判定に由来する未解決の指摘のうち、解消条件（再判定で項目が fail でなく、問題が
issues に挙がらない）を満たし、対象が指摘より新しい版になったものを resolved にする。
"""

import re
from pathlib import Path

from . import finding_cmd, judgment, run_cmd, run_inputs, runs, yamlio
from .run_outputs import STAGE_KINDS
from .run_store import load

STAGE_OF = {k: s for s, kinds in STAGE_KINDS.items() for k in kinds}
ART = re.compile(r"(?<![0-9A-Za-z_-])(ART-[0-9]{3})(?![0-9])")
ORIGIN = f"{judgment.FILE} "  # 判定に由来する指摘の basis の先頭（後ろに項目IDが続く）


def _target(run: dict, basis: list[str]) -> dict:
    """根拠に挙がった最初の登録済み成果物。なければ評価の記録（ia_review）を対象にする。"""
    arts = {a["artifact_id"]: a for a in run["artifacts"] if a["status"] == "current"}
    for b in basis:
        for aid in ART.findall(b):
            if aid in arts:
                return arts[aid]
    return next(a for a in run["artifacts"] if a["kind"] == "ia_review")


def _findings(directory: Path) -> list[dict]:
    path = directory / finding_cmd.NAME
    return yamlio.load(path)["findings"] if path.is_file() else []


def settle(directory: Path, j: dict) -> list[str]:
    """判定に由来する open の指摘のうち、この判定で解消条件を満たし、対象の版が進んだものを resolved にする。"""
    failing = {i["item"] for i in j["items"] if i["result"] == "fail"}
    raised = {i["problem"] for i in j["issues"]}
    run, done = load(directory, "run.yaml"), []
    current = {a["artifact_id"]: a["version"] for a in run["artifacts"] if a["status"] == "current"}
    for f in _findings(directory):
        origin = next((b[len(ORIGIN):] for b in f["basis"] if b.startswith(ORIGIN)), None)
        if (f["status"] != "open" or origin is None or origin in failing or f["problem"] in raised
                or current.get(f["target"]["artifact_id"], 0) <= f["target"]["version"]):
            continue
        finding_cmd.resolve(directory, f["finding_id"], "resolved",
                            note=f"再判定（{j['judge']['host']}/{j['judge']['model']}）で {origin} が fail でなく、問題が issues に挙がらない")
        done.append(f["finding_id"])
    if done:
        reason = f"独立判定の再判定で、判定に由来する指摘 {done} を resolved にした。指摘の状態だけが変わった"
        before = {a["artifact_id"] for a in run["artifacts"] if a["status"] == "stale"}
        run_cmd.artifact(directory, finding_cmd.NAME, "review_findings", [], [])
        _reconfirm(directory, before, reason)
        if run["status"] in ("completed", "unresolved") and load(directory, "run.yaml")["status"] != run["status"]:
            run_inputs.event(directory, "completed", reason, status=run["status"])
    return done


def _reconfirm(directory: Path, before: set[str], reason: str) -> None:
    """指摘の状態の変更で stale になった成果物を、内容を変えずに影響なしとして登録し直す（後続も順に）。"""
    while True:
        run = load(directory, "run.yaml")
        a = next((a for a in run["artifacts"] if a["status"] == "stale" and a["artifact_id"] not in before), None)
        if a is None:
            return
        run_cmd.artifact(directory, a["path"], a["kind"], [d["ref"] for d in a["depends_on"]],
                         [f"{r['decision_id']}:{r['decision_revision']}" for r in a["decision_refs"]], no_impact=reason)


def to_findings(directory: Path, j: dict) -> dict:
    """判定の問題と、問題に挙がっていない fail の項目を指摘として加え、findings.yaml を登録し直す。

    解決していない同じ指摘は加えずに挙げる。Refineが上限に達していれば、指摘を unresolved にして未解決で終える。
    """
    run = load(directory, "run.yaml")
    known = {(f["target"]["artifact_id"], f["problem"]): f["finding_id"]
             for f in _findings(directory) if f["status"] != "resolved"}
    covered = {i["item"] for i in j["issues"]}
    entries = [(i["severity"], i["item"], i["problem"], i["basis"], i["residual"]) for i in j["issues"]]
    entries += [("major", i["item"], f"独立判定で {i['item']} が fail: {i['note'] or '基準を満たさない'}", i["basis"], None)
                for i in j["items"] if i["result"] == "fail" and i["item"] not in covered]
    added, existing = [], []
    for severity, item, problem, basis, residual in entries:
        art = _target(run, basis)
        if (art["artifact_id"], problem) in known:
            existing.append(known[(art["artifact_id"], problem)])
            continue
        locator = next((b for b in basis if art["artifact_id"] in b), item)
        added.append(finding_cmd.add(directory, {
            "severity": severity, "target": {"artifact_id": art["artifact_id"]}, "locator": locator,
            "problem": problem, "basis": [f"{ORIGIN}{item}", *basis],
            "recommendation": f"独立判定の {item} の基準を満たすよう修正する" + (f"（判定の residual: {residual}）" if residual else ""),
            "resolution_condition": f"再判定で {item} が fail にならず、この問題が issues に挙がらない",
            "return_to": STAGE_OF[art["kind"]]}, j["rubric_version"])["finding_id"])
    ids = added + sorted(set(existing) - set(added))
    exhausted = run["refine"]["rounds"] >= runs.MAX_REFINE_ROUNDS
    if run["status"] in ("completed", "unresolved"):
        run_inputs.event(directory, "rerun", f"独立判定 fail の指摘 {ids} をRefineで扱う")
    if exhausted:
        for fid in ids:
            finding_cmd.resolve(directory, fid, "unresolved", note=f"Refineは{runs.MAX_REFINE_ROUNDS}巡を終えている（独立判定）")
    if added or exhausted:
        run_cmd.artifact(directory, finding_cmd.NAME, "review_findings", [], [])
    if exhausted:
        run_inputs.event(directory, "completed", f"Refineは{runs.MAX_REFINE_ROUNDS}巡を終えている。独立判定 fail の"
                         f"指摘 {ids} を未解決として報告する", status="unresolved")
    return {"added": added, "existing": sorted(set(existing)), "status": load(directory, "run.yaml")["status"]}
