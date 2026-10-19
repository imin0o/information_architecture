"""段階ごとの契約検査と自己修正の上限（P3-1）。

一括実行では、段階の終わりに契約検査を行い、失敗すればエージェントが誤りを直して再検査する。同じ段階で
同じ誤り（誤りの一覧が同一）が3回続いた場合は、直せないものとして中断を記録する。誤りが変われば数え直す。
"""

import hashlib
from pathlib import Path

from . import contracts, run_inputs
from .run_store import load, save

LIMIT = 3


def _signature(errors: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(errors)).encode("utf-8")).hexdigest()[:16]


def gate(directory: Path, stage: str) -> dict:
    run = load(directory, "run.yaml")
    if run["status"] in ("completed", "unresolved", "interrupted"):
        raise ValueError(f"実行は {run['status']}。再開してから検査する（iakit run event <dir> resumed）")
    checked = contracts.check_bundle(directory)
    errors = checked.get("errors", [])
    previous = run.get("gate") or {}
    if checked["result"] == "pass":
        run["gate"] = {"stage": stage, "signature": None, "failures": 0}
        save(directory, "run.yaml", run)
        return {"result": "pass", "stage": stage}
    sig = _signature(errors)
    same = previous.get("stage") == stage and previous.get("signature") == sig
    failures = previous["failures"] + 1 if same else 1
    run["gate"] = {"stage": stage, "signature": sig, "failures": failures}
    save(directory, "run.yaml", run)
    out = {"result": "fail", "stage": stage, "failures": failures, "limit": LIMIT, "errors": errors}
    if failures >= LIMIT:
        note = f"段階 {stage} の契約検査が同じ誤りで{LIMIT}回続けて失敗した: " + " / ".join(errors[:3])
        run_inputs.event(directory, "interrupted", note)
        out.update(result="interrupted", note=note)
    return out
