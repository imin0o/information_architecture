"""段階ごとの契約検査と自己修正の上限（P3-1）。

一括実行では、段階の終わりに契約検査を行い、失敗すればエージェントが誤りを直して再検査する。同じ段階で
同じ誤り（誤りの一覧が同一）が3回続いた場合は、直せないものとして中断を記録する。誤りが変われば数え直す。
誤りが入れ替わり続けて止まらない場合に備え、同じ段階の失敗の合計にも上限を置く。再開（resumed）で数え直す。
"""

import hashlib
from pathlib import Path

from . import contracts, run_inputs
from .run_store import load, save

LIMIT = 3
TOTAL_LIMIT = 10  # 誤りが変わっても、同じ段階でこの回数失敗すれば中断する


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
        run["gate"] = {"stage": stage, "signature": None, "failures": 0, "total": 0}
        save(directory, "run.yaml", run)
        return {"result": "pass", "stage": stage}
    sig = _signature(errors)
    same_stage = previous.get("stage") == stage
    failures = previous["failures"] + 1 if same_stage and previous.get("signature") == sig else 1
    total = previous.get("total", previous.get("failures", 0)) + 1 if same_stage else 1
    run["gate"] = {"stage": stage, "signature": sig, "failures": failures, "total": total}
    save(directory, "run.yaml", run)
    out = {"result": "fail", "stage": stage, "failures": failures, "limit": LIMIT, "total": total,
           "total_limit": TOTAL_LIMIT, "errors": errors}
    if failures >= LIMIT or total >= TOTAL_LIMIT:
        cause = f"同じ誤りで{LIMIT}回続けて" if failures >= LIMIT else f"誤りを変えながら{TOTAL_LIMIT}回"
        note = f"段階 {stage} の契約検査が{cause}失敗した: " + " / ".join(errors[:3])
        run_inputs.event(directory, "interrupted", note)
        out.update(result="interrupted", note=note)
    return out
