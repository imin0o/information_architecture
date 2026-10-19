"""一括実行の受け入れ（P3の完了条件、FR-08、AC-09）。

依頼を渡してから completed または unresolved になるまで、人の操作なしで進むことを確かめる。配置先の配布物で
ホストに一括実行（ia-orchestrator 手順5a）を依頼する。送信の許可は依頼文に書かず、常設の許可のプロファイル
（IAKIT_PROFILE）だけで与える。回収した実行に iakit run judge で相手のホストの独立判定を受け、fail なら
同じホストへRefineを依頼して判定を受け直す。最後に iakit run report で報告を作る。
"""

import os

from . import accept, accept_route, contracts, judge_cmd, judgment, report, standing, yamlio
from . import accept_hostenv as accept_host
from .paths import ROOT

FULL = ("情報設計の標準経路を、ia-orchestratorの一括実行（手順5a）で最後まで実行して。実行IDは {run}、"
        "実行ディレクトリは runs/{run}。質問への回答はこの場では得られないため、Skillの手順どおり仮定を置いて進めて。"
        "独立判定（手順9）は別のエージェントが後で行うので実施しないで。終えたら iakit run report で報告を作り、"
        "成果物の一覧と版、仮定、残る指摘を報告して。")
REFINE = ("runs/{run} は標準経路を終えた実行で、別のエージェントによる独立判定が fail だった。判定の問題と fail の項目は"
          "findings.yaml の指摘として登録済みで、実行は in_progress に戻してある。ia-orchestratorの手順どおり、Refineの"
          "上限内で修正と再評価を行って終えて（上限に達していれば unresolved で終える）。独立判定は別のエージェントが"
          "後でやり直すので実施しないで。rubric-judgment*.yaml は変更しないで。終えたら iakit run report で報告を作り直して。")
LIMIT = 2  # 判定を受け直す回数の上限（Refineの上限と同じ）
GRANTED = "2026-10-20T00:00:00+09:00"


def _profile(path, host: str) -> None:
    """受け入れ用の常設の許可。範囲は4章の決定（ホストのLLMと判定の相手、internal まで、入力と派生物）。"""
    me = accept_host.LAYOUT[host]
    entries = [{"entry": "host-llm", "destination": me, "destination_kind": "host_llm"},
               {"entry": "judge", "destination": standing.partner(me), "destination_kind": "external_service"}]
    path.write_text(yamlio.dumps({"schema_version": "1.0", "approvals": [
        {**e, "max_classification": "internal", "data_kinds": ["input", "derived"],
         "purpose": "一括実行の受け入れ（P3）", "granted_at": GRANTED} for e in entries]}), encoding="utf-8")


def _human_items(directory) -> list[str]:
    text = (directory / report.NAME).read_text(encoding="utf-8")
    section = text.split("## 人の判断が必要な事項", 1)[-1].split("\n## ", 1)[0]
    return [line[2:] for line in section.splitlines() if line.startswith("- ")]


def _judge(dest, case: str) -> dict:
    j = judge_cmd.judge(dest, case)
    return {"check": "judge", **{k: j.get(k) for k in ("result", "judge_host", "exit_code", "verdict", "findings_added",
                                                         "findings_existing", "findings_resolved", "status", "changed")},
            "judge_model": (j.get("judge") or {}).get("model"), "errors": j.get("errors", [])[:5]}


def auto(host: str, case: str, run_id: str, timeout: int = 7200) -> dict:
    dest = ROOT / "runs" / run_id
    base = accept.tempdir("iakit p3 auto ")
    profile = base / "approvals.yaml"
    _profile(profile, host)
    env = {"IAKIT_PROFILE": str(profile)}
    request = accept_route.REQUESTS[case].replace(accept_host.ALLOW_TEXT, "")
    previous = os.environ.get("IAKIT_PROFILE")
    os.environ.update(env)  # 回収後の iakit run judge も同じプロファイルを使う
    steps, errors = [], []
    try:
        step = accept_route.route(host, case, run_id, timeout=timeout, prompt=request + FULL.format(run=run_id), env=env)
        steps.append(step)
        for i in range(LIMIT + 1):
            if step["result"] != "pass":
                errors.append(f"ホストの実行（{step.get('mode', 'full')}）が失敗した: {step.get('errors', [])[:3]}")
                break
            judged = _judge(dest, case)
            steps.append(judged)
            if judged["result"] != "pass" or judged["verdict"] == "pass":
                if judged["result"] != "pass":
                    errors.append(f"独立判定が {judged['result']}: {judged['errors'] or judged.get('verdict')}")
                break
            if judged["status"] == "unresolved":
                break  # Refineの上限に達していたため、判定のコマンドが未解決で終えた
            if i == LIMIT:
                errors.append("独立判定が fail のまま、判定を受け直す回数の上限に達した")
                break
            rounds = yamlio.load(dest / "run.yaml")["refine"]["rounds"]
            step = accept_route.route(host, case, run_id, refine=True, timeout=timeout,
                                      prompt=REFINE.format(run=run_id), env=env)
            steps.append(step)
            if step["result"] == "pass":
                refined = yamlio.load(dest / "run.yaml")
                if refined["status"] == "unresolved":
                    break
                if refined["refine"]["rounds"] <= rounds:
                    errors.append("独立判定が fail のまま、Refineの巡数が増えずに終了した")
                    break
    finally:
        if previous is None:
            os.environ.pop("IAKIT_PROFILE", None)
        else:
            os.environ["IAKIT_PROFILE"] = previous
        left = accept.remove(base)
    details = {}
    if (dest / "run.yaml").is_file():
        report.write(dest)
        run = yamlio.load(dest / "run.yaml")
        check = contracts.check_bundle(dest)
        details = {"status": run["status"], "refine_rounds": run["refine"]["rounds"], "contracts": check["result"],
                   "standing_approvals": sorted(a["standing"]["entry"] for a in run["approvals"] if a.get("standing")),
                   "human_items": _human_items(dest)}
        if run["status"] not in ("completed", "unresolved"):
            errors.append(f"実行が終了していない: {run['status']}")
        if check["result"] != "pass":
            errors += check.get("errors", [])[:10]
        if run["status"] == "completed":
            final = judgment.check(dest, case)
            details["judgment"] = final["result"]
            if final["result"] != "pass":
                errors += final["errors"][:10]
            elif yamlio.load(dest / judgment.FILE)["verdict"] != "pass":
                errors.append("completed の実行に、現行版の独立判定 pass がない")
    else:
        errors.append(f"実行記録がない: {dest / 'run.yaml'}")
    # 依頼文のほかに人が渡したものはない。ホストへの指示は FULL と REFINE の固定文だけである。
    result = {"check": "accept_auto", "host": host, "case": case, "run_id": run_id, "human_operations": 0,
              "steps": steps, **details, "left_behind": left, "result": "fail" if errors else "pass"}
    if errors:
        result["errors"] = errors
    return result
