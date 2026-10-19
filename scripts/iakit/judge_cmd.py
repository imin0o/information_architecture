"""独立判定の自動実行（P3-3、AC-09）。

成果物を作ったホストと別のホスト（Claude Code と Codex の相互）を起動し、固定ケースのルーブリックで判定させる。
送信は run.yaml の approvals（常設の許可を含む）の範囲に限り、許可がなければ起動せず not_run とする。
判定の記録を iakit run judgment と同じ検査にかけ、fail の問題と fail の項目を findings.yaml の指摘としてRefineへ戻す。
依頼文の正本は skills/ia-orchestrator/references/independent-review.md の「依頼文」節にある。
"""

import re
import shutil
import subprocess
from pathlib import Path

from . import finding_cmd, judgment, run_cmd, run_inputs, standing, yamlio
from .decisions import RANK, max_classification
from .paths import SKILLS
from .run_outputs import STAGE_KINDS
from .run_store import load, now, save

REFERENCE = SKILLS / "ia-orchestrator" / "references" / "independent-review.md"
# 判定者の起動（references/independent-review.md の表）。書いてよいのは判定の記録だけで、依頼文で制限する
COMMANDS = {
    "claude-code": lambda cwd, prompt: ["claude", "-p", prompt, "--output-format", "text", "--permission-mode",
                                        "acceptEdits", "--allowedTools", "Bash(uv run:*)", "Read", "Glob", "Grep",
                                        "Write", "Edit"],
    "codex": lambda cwd, prompt: ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write", "-C", str(cwd),
                                  prompt],
}
STAGE_OF = {k: s for s, kinds in STAGE_KINDS.items() for k in kinds}
ART = re.compile(r"(?<![0-9A-Za-z_-])(ART-[0-9]{3})(?![0-9])")


def request_text(run_path: str, run_id: str, case: str) -> str:
    text = REFERENCE.read_text(encoding="utf-8")
    body = re.search(r"## 依頼文\n.*?```text\n(.*?)```", text, re.S)
    if body is None:
        raise ValueError(f"{REFERENCE} に依頼文の節がない")
    return body.group(1).replace("runs/<run_id>", run_path).replace("<run_id>", run_id).replace("<case>", case)


def _approval(run: dict, inputs: list[dict], judge: str) -> tuple[dict | None, str]:
    """判定者に入力と派生物を読ませる許可。最も厳しい区分が許可の上限以下で、confidential でないこと。"""
    level = max_classification([i["classification"] for i in inputs]
                               + [a["classification"] for a in run["artifacts"] if a["status"] == "current"])
    if level == "confidential":
        return None, "confidential の入力または派生物を含むため、判定を依頼しない"
    ids = {i["input_id"] for i in inputs}
    for a in run["approvals"]:
        if ((a["destination"], a["destination_kind"]) == (judge, "external_service")
                and {"input", "derived"} <= set(a["data_kinds"]) and ids <= set(a["input_ids"])
                and RANK[level] <= RANK[a["max_classification"]]):
            return a, level
    return None, (f"{judge} へ入力と派生物（区分 {level}）を送る許可が approvals にない。"
                  "常設の許可を iakit run approvals で写すか、ユーザーの許可を記録する")


def _call(host: str, cwd: Path, prompt: str, timeout: int) -> tuple[int, str]:
    """判定者は作業ディレクトリで iakit run judgment を使うため、環境変数を絞らずに起動する。"""
    args = COMMANDS[host](cwd, prompt)
    exe = shutil.which(args[0])
    if exe is None:
        return 127, ""
    try:
        done = subprocess.run([exe, *args[1:]], cwd=cwd, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return done.returncode, done.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def _history(directory: Path, note: str) -> None:
    run = load(directory, "run.yaml")
    run["history"].append({"event": "judged", "at": now(), "note": note})
    save(directory, "run.yaml", run)


def _target(run: dict, basis: list[str]) -> dict:
    """根拠に挙がった最初の登録済み成果物。なければ評価の記録（ia_review）を対象にする。"""
    arts = {a["artifact_id"]: a for a in run["artifacts"] if a["status"] == "current"}
    for b in basis:
        for aid in ART.findall(b):
            if aid in arts:
                return arts[aid]
    return next(a for a in run["artifacts"] if a["kind"] == "ia_review")


def to_findings(directory: Path, j: dict) -> list[str]:
    """判定の問題と、問題に挙がっていない fail の項目を指摘として加え、findings.yaml を登録し直す。"""
    run = load(directory, "run.yaml")
    covered = {i["item"] for i in j["issues"]}
    entries = [(i["severity"], i["item"], i["problem"], i["basis"], i["residual"]) for i in j["issues"]]
    entries += [("major", i["item"], f"独立判定で {i['item']} が fail: {i['note'] or '基準を満たさない'}", i["basis"], None)
                for i in j["items"] if i["result"] == "fail" and i["item"] not in covered]
    added = []
    for severity, item, problem, basis, residual in entries:
        art = _target(run, basis)
        locator = next((b for b in basis if art["artifact_id"] in b), item)
        added.append(finding_cmd.add(directory, {
            "severity": severity, "target": {"artifact_id": art["artifact_id"]}, "locator": locator,
            "problem": problem, "basis": [f"rubric-judgment.yaml {item}", *basis],
            "recommendation": f"独立判定の {item} の基準を満たすよう修正する" + (f"（判定の residual: {residual}）" if residual else ""),
            "resolution_condition": f"再判定で {item} が fail にならず、この問題が issues に挙がらない",
            "return_to": STAGE_OF[art["kind"]]}, j["rubric_version"])["finding_id"])
    if added:
        if run["status"] in ("completed", "unresolved"):
            run_inputs.event(directory, "rerun", f"独立判定 fail の指摘 {added} をRefineで扱う")
        run_cmd.artifact(directory, "findings.yaml", "review_findings", [], [])
    return added


def judge(directory: Path, case: str, timeout: int = 3600, call=_call) -> dict:
    synced = standing.sync(directory)
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")["inputs"]
    host = run["environment"]["host"]
    judge_host = standing.partner(host)
    if judge_host is None:
        raise ValueError(f"ホスト {host} の独立判定の相手が決まらない（claude-code・codex の実行だけ自動判定できる）")
    if run["status"] not in ("completed", "unresolved"):
        raise ValueError(f"実行は {run['status']}。標準経路を終えてから判定を受ける")
    approval, level = _approval(run, inputs, judge_host)
    if approval is None:
        _history(directory, f"not_run: {level}")
        return {"result": "not_run", "judge_host": judge_host, "reason": level, "standing": synced}
    path = directory / judgment.FILE
    if path.is_file():
        n = sum(1 for _ in directory.glob("rubric-judgment.round*.yaml")) + 1
        path.rename(directory / f"rubric-judgment.round{n}.yaml")
    cwd = Path.cwd()
    try:
        rel = directory.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        rel = directory.resolve().as_posix()
    code, out = call(judge_host, cwd, request_text(rel, run["run_id"], case), timeout)
    if code != 127:
        # 起動できた時点で成果物と入力は判定者の提供元へ渡る
        run = load(directory, "run.yaml")
        run["transmissions"] += [{"destination": judge_host, "destination_kind": "external_service", "data_kind": kind,
                                  "input_ids": [i["input_id"] for i in inputs], "approval_id": approval["approval_id"],
                                  "outcome": "sent"} for kind in ("input", "derived")]
        save(directory, "run.yaml", run)
    (directory / "judge-message.txt").write_text(out, encoding="utf-8", newline="\n")
    checked = judgment.check(directory, case) if path.is_file() else {
        "check": "rubric_judgment", "result": "fail", "errors": [f"判定者が {judgment.FILE} を書かなかった（終了コード {code}）"]}
    j = yamlio.load(path) if checked["result"] == "pass" else None
    who = f"{judge_host}/{j['judge']['model']}" if j else judge_host
    _history(directory, f"{who} verdict={j['verdict'] if j else '-'} check={checked['result']} exit={code}")
    result = {"result": checked["result"], "judge_host": judge_host, "exit_code": code, "errors": checked["errors"]}
    if j:
        result.update(verdict=j["verdict"], judge=j["judge"], findings_added=[] if j["verdict"] == "pass" else to_findings(directory, j))
    return result
