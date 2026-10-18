"""代表ケースを配置先の配布物で最後まで実行する（M6、AC-06、AC-09、AC-10、AC-12）。

検査済みの dist/ をビルド元の外（空白を含むパス）へホストの配置先として写し、課題ケースの入力だけを置いて
ホストCLIに標準経路の実行を依頼する。ケースの合格条件は依頼文に含めない。終了後に実行ディレクトリを
runs/<run_id>/ へ回収し（Git管理外）、契約検査の結果と本文を含まない要約を返す。独立判定は、成果物を作った
ホストと別のホストで後から行うため、実行するホストには依頼しない。一時ディレクトリは終了時に削除する。
"""

import shutil
import time

from . import accept, contracts, yamlio
from . import accept_hostenv as accept_host
from .paths import ROOT

REQUESTS = {
    "webia-001": accept_host.WEBIA,
    "taxonomy-001": ("架空の生活支援サービス事業者で、サイトとパンフレットに使う用語を統一したい。依頼の説明は"
                     "「input files/brief.md」、候補語は「input files/concepts.csv」にある。" + accept_host.ALLOW_TEXT),
}
JUDGE = {"claude": "OpenAI（Codex）", "codex": "Anthropic（Claude Code）"}
FULL = ("情報設計の標準経路を最後まで実行して。実行IDは {run}、実行ディレクトリは runs/{run}。"
        "質問への回答はこの場では得られないため、Skillの手順どおり仮定を置いて進めて。評価の指摘への修正と再評価"
        "（Refine）もSkillの手順どおり行って。独立判定（手順9）は別のエージェントが後で行うので実施しないで。"
        "成果物と入力を独立判定のため{judge}へ送ることは許可する（区分internalまで）。"
        "終えたら成果物の一覧と版、仮定、残る指摘を報告して。")
REFINE = ("runs/{run} は標準経路を終えた実行で、別のエージェントによる独立判定 runs/{run}/rubric-judgment.yaml が fail"
          "だった。ia-orchestratorの手順（references/independent-review.md の「判定の後」）に従い、判定の問題と fail の"
          "項目を findings.yaml の指摘として加え、Refineの上限内で修正と再評価を行って終えて。独立判定は別のエージェントが"
          "後でやり直すので実施しないで。rubric-judgment.yaml は変更しないで。終えたら変更した成果物と版、残る指摘を報告して。")


def route(host: str, case: str, run_id: str, refine: bool = False, timeout: int = 7200) -> dict:
    """refine が真なら、回収済みの実行と fail の判定を配置先へ戻し、判定を受けたRefineを依頼する。"""
    dest = ROOT / "runs" / run_id
    if dest.exists() != refine or refine and not (dest / "rubric-judgment.yaml").is_file():
        return {"check": "accept_route", "result": "fail",
                "errors": [f"{dest} は{'判定済みの実行がない' if refine else '既にある'}"]}
    base = accept.tempdir(f"iakit m6 route {host} ")
    try:
        workspace, skills = accept_host._workspace(base, host, None, case)
        rounds = 0
        if refine:
            shutil.copytree(dest, workspace / "runs" / run_id)
            rounds = yamlio.load(dest / "run.yaml")["refine"]["rounds"]
        prompt = REQUESTS[case] + (REFINE if refine else FULL).format(run=run_id, judge=JUDGE[host])
        started = time.monotonic()
        code, out = accept_host._host_call(host, workspace, prompt, timeout)
        minutes = round((time.monotonic() - started) / 60, 1)
        produced = workspace / "runs" / run_id
        if produced.is_dir():
            if refine:  # 判定前の版を残す。旧い判定は現行版を対象にしないため名前を変える
                (produced / "rubric-judgment.yaml").rename(produced / f"rubric-judgment.round{rounds}.yaml")
                (produced / "host-final-message.txt").rename(produced / f"host-final-message.round{rounds}.txt")
                shutil.rmtree(dest)
            shutil.copytree(produced, dest)
            (dest / "host-final-message.txt").write_text(out, encoding="utf-8", newline="\n")
        started_obs = accept_host._started(workspace, skills, run_id, yamlio.load(
            ROOT / "tests" / "cases" / case / "case.yaml")["task_type"], 2)
    finally:
        left = accept.remove(base)
    result = {"check": "accept_route", "mode": "refine" if refine else "full", "host": host, "case": case, "run_id": run_id, "exit_code": code,
              "minutes": minutes, "placement": f"<base with spaces>/workspace/{accept.LAYOUTS[accept_host.LAYOUT[host]]}",
              "distribution_ids": {p.parent.name: yamlio.load(p)["distribution_id"]
                                   for p in sorted(accept.DIST.glob("*/build-manifest.yaml"))},
              "started_with_bundled_runtime": started_obs, "left_behind": left}
    if not dest.is_dir():
        result.update(result="fail", errors=["実行ディレクトリが作られなかった"])
        return result
    run = yamlio.load(dest / "run.yaml")
    check = contracts.check_bundle(dest)
    result.update(status=run["status"], refine_rounds=run["refine"]["rounds"],
                  model=run["environment"]["model"], contracts=check["result"],
                  result="pass" if code == 0 and run["status"] in ("completed", "unresolved")
                  and check["result"] == "pass" else "fail")
    if check["result"] != "pass":
        result["errors"] = check.get("errors", [])[:10]
    return result
