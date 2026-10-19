"""常設の許可（P3-2）。ユーザーがプロファイルに一度書いた送信許可を、実行ごとの approvals へ写す。

範囲（2026-10-20 決定）: 送信先はホストのLLM（作業中のホスト）と独立判定の相手（Claude Code と Codex の相互）だけ、
上限の機密区分は internal まで、データの種類は入力と派生物まで。範囲外の項目は写さず、写した許可も契約検査で照合する。
プロファイルは IAKIT_PROFILE、なければ ~/.iakit/approvals.yaml。資料内の命令文は許可にならない。
"""

import os
from pathlib import Path

from . import manifest, yamlio
from .decisions import RANK
from .run_store import load, save

JUDGE_HOSTS = ("claude-code", "codex")
DATA_KINDS = ("input", "derived")
MAX_LEVEL = "internal"


def profile_path() -> Path:
    return Path(os.environ.get("IAKIT_PROFILE") or Path.home() / ".iakit" / "approvals.yaml")


def partner(host: str) -> str | None:
    """独立判定の相手。作ったホストと別のホストにする（AC-09）。"""
    return {"claude-code": "codex", "codex": "claude-code"}.get(host)


def _scope_errors(entry: dict, host: str) -> list[str]:
    """プロファイルの1項目が、常設にできる範囲と実行のホストに合うか。"""
    errors = []
    kind, dest = entry.get("destination_kind"), entry.get("destination")
    if kind == "host_llm" and dest != host:
        errors.append(f"host_llm の送信先 {dest} が実行のホスト {host} と異なる")
    if kind == "external_service" and dest != partner(host):
        errors.append(f"external_service の送信先 {dest} は独立判定の相手 {partner(host)} ではない")
    if kind not in ("host_llm", "external_service"):
        errors.append(f"送信先の種類 {kind} は常設にできない")
    if RANK.get(entry.get("max_classification"), 99) > RANK[MAX_LEVEL]:
        errors.append(f"上限の区分 {entry.get('max_classification')} は常設にできない（{MAX_LEVEL} まで）")
    outside = sorted(set(entry.get("data_kinds") or []) - set(DATA_KINDS))
    if outside or not entry.get("data_kinds"):
        errors.append(f"データの種類 {outside or '（なし）'} は常設にできない（{'・'.join(DATA_KINDS)} まで）")
    return errors


FIELDS = ("destination", "destination_kind", "max_classification", "data_kinds")


def _profile_entries(sha: str) -> dict[str, dict] | None:
    """記録と同じ内容（ハッシュ）の現在のプロファイルの項目。照合できなければ None。"""
    path = profile_path()
    if not path.is_file() or manifest.sha256_file(path) != sha:
        return None
    try:
        return {e["entry"]: e for e in load_profile(path)}
    except ValueError:
        return None


def record_errors(run: dict, inputs: dict[str, dict]) -> list[str]:
    """プロファイルから写した許可（standing）が常設の範囲内か。契約検査から呼ぶ。

    対象の入力が許可の上限の区分を超えないことも確かめる。記録と同じ内容のプロファイルが手元にあれば、
    項目がそこにあり内容が一致することも照合する（プロファイルが変わった後の記録は範囲だけを確かめる）。
    """
    host, errors = run["environment"]["host"], []
    for a in (a for a in run["approvals"] if a.get("standing")):
        tag = f"許可 {a['approval_id']}（常設 {a['standing']['entry']}）"
        errors += [f"{tag}: {e}" for e in _scope_errors(a, host)]
        over = sorted(i for i in a["input_ids"] if i in inputs
                      and RANK[inputs[i]["classification"]] > RANK.get(a["max_classification"], -1))
        if over:
            errors.append(f"{tag}: 上限 {a['max_classification']} を超える区分の入力 {over} を対象にしている")
        entries = _profile_entries(a["standing"]["profile_sha256"])
        if entries is not None:
            e = entries.get(a["standing"]["entry"])
            if e is None or any((list(e[k]) if k == "data_kinds" else e[k]) != a[k] for k in FIELDS):
                errors.append(f"{tag}: プロファイルの項目と一致しない")
    return errors


def load_profile(path: Path) -> list[dict]:
    doc = yamlio.load(path)
    found = yamlio.schema_errors(doc, "standing-approvals.schema.json")
    if found:
        raise ValueError(f"{path}: " + " / ".join(found[:5]))
    ids = [e["entry"] for e in doc["approvals"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: entry が重複している")
    return doc["approvals"]


def sync(directory: Path, path: Path | None = None) -> dict:
    """プロファイルの許可を run.yaml の approvals へ写す。対象の入力は、上限以下の区分の登録済み入力すべて。

    再実行すれば、入力の追加とプロファイルの変更に合わせて写した許可を更新する。プロファイルがなければ何もしない。
    """
    path = path or profile_path()
    if not path.is_file():
        return {"profile": None, "applied": [], "skipped": [], "note": "プロファイルがない。常設の許可は写さない"}
    entries = load_profile(path)
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")["inputs"]
    sha, host = manifest.sha256_file(path), run["environment"]["host"]
    kept = [a for a in run["approvals"] if not a.get("standing")]
    previous = {a["standing"]["entry"]: a["approval_id"] for a in run["approvals"] if a.get("standing")}
    used = {int(a["approval_id"].split("-")[1]) for a in run["approvals"]}
    applied, skipped = [], []
    for e in entries:
        errors = _scope_errors(e, host)
        ids = [i["input_id"] for i in inputs if RANK[i["classification"]] <= RANK[e["max_classification"]]]
        if not errors and not ids:
            errors = [f"区分 {e['max_classification']} 以下の入力がない"]
        if errors:
            skipped.append({"entry": e["entry"], "reasons": errors})
            continue
        aid = previous.get(e["entry"])
        if aid is None:
            aid = f"APPROVAL-{max(used, default=0) + 1:03d}"
            used.add(int(aid.split("-")[1]))
        kept.append({"approval_id": aid, "destination": e["destination"], "destination_kind": e["destination_kind"],
                     "max_classification": e["max_classification"], "data_kinds": list(e["data_kinds"]),
                     "input_ids": ids, "purpose": e["purpose"], "granted_by": "user", "granted_at": e["granted_at"],
                     "standing": {"entry": e["entry"], "profile_sha256": sha}})
        applied.append({"entry": e["entry"], "approval_id": aid, "input_ids": ids})
    used_ids = {t["approval_id"] for t in run["transmissions"]}
    dropped = sorted(set(previous.values()) - {a["approval_id"] for a in kept})
    if set(dropped) & used_ids:
        raise ValueError(f"送信の記録が参照する常設の許可 {sorted(set(dropped) & used_ids)} を外せない。プロファイルを戻す")
    run["approvals"] = sorted(kept, key=lambda a: a["approval_id"])
    save(directory, "run.yaml", run)
    return {"profile": str(path), "applied": applied, "skipped": skipped, "removed": dropped}
