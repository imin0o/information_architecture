"""成果物の登録時の検査と、本体から読み取る参照（P1）。登録を拒否する検査は契約検査より前にここで行う。"""

from pathlib import Path

from . import run_outputs
from .run_outputs import DECISION_REF
REF_KINDS = {"inventory": "content_inventory", "vocabulary": "controlled_vocabulary"}


def contract_check(directory: Path, run: dict, entry: dict, decisions: list[dict]) -> None:
    """登録する成果物だけを対象に、Markdownの契約と仮定の明示を登録時に検査する。"""
    trial = dict(run, artifacts=[a for a in run["artifacts"] if a["artifact_id"] != entry["artifact_id"]] + [entry])
    tag = f"{entry['artifact_id']}（"
    found = [e for e in run_outputs.markdown_errors(directory, trial, decisions)
             + run_outputs.assumption_errors(directory, trial, decisions)
             if e.startswith(tag) or (entry["kind"] == "validation_plan" and e.startswith("validation_plan:"))]
    if found:
        raise ValueError("登録しない（契約に合わない）: " + " / ".join(found[:5]))


def ref_kind_check(kind: str, body, artifacts: list[dict]) -> None:
    """分類体系・サイトマップの参照先が、その型の登録済み成果物であることを登録時に確かめる。"""
    if kind not in ("taxonomy", "sitemap"):
        return
    kinds = {a["artifact_id"]: a["kind"] for a in artifacts}
    for key, expected in REF_KINDS.items():
        ref = body.get(key) if isinstance(body, dict) else None
        if isinstance(ref, dict) and kinds.get(ref.get("artifact_id")) != expected:
            right = next((a["artifact_id"] for a in artifacts if a["kind"] == expected), None)
            raise ValueError(f"{key} の参照 {ref.get('artifact_id')} は {expected} ではない"
                             + (f"（登録済みの {expected} は {right}）" if right else f"（{expected} を先に登録する）"))


def body_refs(kind: str, body, text: str) -> tuple[set[str], list[tuple[str, int]]]:
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
