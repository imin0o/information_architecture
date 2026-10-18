"""代表ケースを配置先の配布物で最後まで実行する（M6、AC-06、AC-09、AC-10、AC-12）。

検査済みの dist/ をビルド元の外（空白を含むパス）へホストの配置先として写し、課題ケースの入力だけを置いて
ホストCLIに標準経路の実行を依頼する。ケースの合格条件は依頼文に含めない。終了後に実行ディレクトリを
runs/<run_id>/ へ回収し（Git管理外）、契約検査の結果と本文を含まない要約を返す。独立判定は、成果物を作った
ホストと別のホストで後から行うため、実行するホストには依頼しない。一時ディレクトリは終了時に削除する。
"""

import shutil
import tempfile
import time
from pathlib import Path

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


def _collect(produced: Path, dest: Path, out: str, rounds: int | None) -> None:
    """回収を完了してから旧実行と入れ替える。失敗時は判定を含む旧実行を保持する。"""
    if rounds is None:
        shutil.copytree(produced, dest)
        (dest / "host-final-message.txt").write_text(out, encoding="utf-8", newline="\n")
        return
    backup = Path(tempfile.mkdtemp(prefix=f".{dest.name}-refine-", dir=dest.parent)).resolve()
    staged, old = backup / "candidate", backup / "previous"
    try:
        shutil.copytree(produced, staged)
        # 判定前の版を残す。旧い判定は現行版を対象にしないため名前を変える。
        (staged / "rubric-judgment.yaml").rename(staged / f"rubric-judgment.round{rounds}.yaml")
        if (staged / "host-final-message.txt").is_file():
            (staged / "host-final-message.txt").rename(staged / f"host-final-message.round{rounds}.txt")
        (staged / "host-final-message.txt").write_text(out, encoding="utf-8", newline="\n")
        dest.rename(old)
        try:
            staged.rename(dest)
        except OSError:
            try:
                old.rename(dest)
            except OSError as exc:
                raise OSError(f"実行の復元に失敗した。退避先 {old} と回収先 {dest} を確認する") from exc
            raise
    finally:
        if not old.exists():
            shutil.rmtree(backup)
    shutil.rmtree(backup)


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
        case_doc = yamlio.load(ROOT / "tests" / "cases" / case / "case.yaml")
        started_obs = accept_host._started(workspace, skills, run_id, case_doc["task_type"], len(case_doc["inputs"]))
        errors, details = [], {}
        if code != 0:
            errors.append(f"ホストの実行に失敗した（終了コード {code}）")
        if not started_obs["run_yaml"]:
            errors.append("実行記録が作られなかった")
        else:
            # 回答などの追加入力は許すが、固定ケースの入力はすべて必要。
            registered = {i["sha256"] for i in yamlio.load(produced / "inputs.yaml")["inputs"]}
            started_obs["inputs_registered"] = all(i["sha256"] in registered for i in case_doc["inputs"])
            for key in ("task_type", "skills_match_placement", "inputs_registered"):
                if not started_obs[key]:
                    errors.append(f"配置先・固定ケースとの照合に失敗: {key}")
            if started_obs["host"] != accept_host.LAYOUT[host]:
                errors.append("実行記録のホストが依頼先と異なる")
            run = yamlio.load(produced / "run.yaml")
            check = contracts.check_bundle(produced)
            details = {"status": run["status"], "refine_rounds": run["refine"]["rounds"],
                       "model": run["environment"]["model"], "contracts": check["result"]}
            if run["status"] not in ("completed", "unresolved"):
                errors.append(f"実行が終了していない: {run['status']}")
            if check["result"] != "pass":
                errors += check.get("errors", [])[:10] or ["契約検査に失敗した"]
        # 失敗したRefineでは元の実行・判定を変えず、同じコマンドを再試行できるようにする。
        if produced.is_dir() and (not refine or not errors):
            _collect(produced, dest, out, rounds if refine else None)
    finally:
        left = accept.remove(base)
    result = {"check": "accept_route", "mode": "refine" if refine else "full", "host": host, "case": case, "run_id": run_id, "exit_code": code,
              "minutes": minutes, "placement": f"<base with spaces>/workspace/{accept.LAYOUTS[accept_host.LAYOUT[host]]}",
              "distribution_ids": {p.parent.name: yamlio.load(p)["distribution_id"]
                                   for p in sorted(accept.DIST.glob("*/build-manifest.yaml"))},
              "started_with_bundled_runtime": started_obs, "left_behind": left,
              **details, "result": "fail" if errors else "pass"}
    if errors:
        result["errors"] = errors
    return result
