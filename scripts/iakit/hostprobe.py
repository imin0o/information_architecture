"""両ホストでのSkill配置確認（M1）。自作の互換性確認用Skillだけを一時ディレクトリへ置く。

確認項目:
  discovery  Skill名を出さずに依頼し、descriptionから選ばれてトークンを返す
  explicit   Skill名を指定して依頼する
  control    Skillを置かずに同じ依頼をし、トークンを返さない（付属資料から読んだことの対照）
トークンは references/ にだけ書かれているため、返答に含まれれば付属資料の参照も確認できる。
応答本文は保存しない。配置した一時ディレクトリは終了時に削除する。
"""

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import ROOT

PROBE = ROOT / "tests" / "host-compat" / "iakit-host-probe"
TOKEN = "IAKIT-HOST-PROBE-4C1E7A"
QUESTION = "What is the iakit host probe token? Answer with the PROBE-RESULT line only."
HOSTS = {
    "claude": {
        "skills_dir": ".claude/skills",
        "version": ["claude", "--version"],
        "command": lambda cwd, prompt: ["claude", "-p", prompt, "--output-format", "text"],
        "explicit": "/iakit-host-probe",
    },
    "codex": {
        "skills_dir": ".agents/skills",
        "version": ["codex", "--version"],
        "command": lambda cwd, prompt: ["codex", "exec", "--skip-git-repo-check", "--ephemeral",
                                        "-s", "read-only", "-C", str(cwd), prompt],
        "explicit": "Use the $iakit-host-probe skill.",
    },
}


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        return proc.returncode, proc.stdout + "\n" + proc.stderr
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def _first_error_line(output: str) -> str | None:
    lines = [ln for ln in output.splitlines() if "error" in ln.lower()]
    return lines[0][:160] if lines else None


def probe(host: str, timeout: int = 300, space_in_path: bool = False) -> dict:
    spec = HOSTS[host]
    code, version = _run(spec["version"], ROOT, 60)
    result = {"host": host, "host_version": version.strip().splitlines()[0] if code == 0 else None,
              "os": f"{platform.system()} {platform.release()}", "placement": f"<workdir>/{spec['skills_dir']}",
              "space_in_path": space_in_path, "checks": {}}
    if code != 0:
        result.update(result="blocked", reason="ホストCLIを実行できない")
        return result
    base = Path(tempfile.mkdtemp(prefix="iakit probe " if space_in_path else "iakit-probe-"))
    try:
        with_skill, without = base / "with skill" if space_in_path else base / "with", base / "control"
        shutil.copytree(PROBE, with_skill / spec["skills_dir"] / PROBE.name)
        without.mkdir()
        for name, cwd, prompt in (("discovery", with_skill, QUESTION),
                                  ("explicit", with_skill, spec["explicit"]),
                                  ("control", without, QUESTION)):
            rc, out = _run(spec["command"](cwd, prompt), cwd, timeout)
            found = f"PROBE-RESULT: {TOKEN}" in out
            entry = {"exit_code": rc, "token_returned": found}
            if rc != 0:
                entry["error"] = _first_error_line(out)
            entry["result"] = "pass" if rc == 0 and found == (name != "control") else "fail"
            result["checks"][name] = entry
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if all(c["exit_code"] != 0 for c in result["checks"].values()):
        result.update(result="blocked", reason="ホストCLIが応答を返さない（設定・認証を確認）")
    else:
        result["result"] = "pass" if all(c["result"] == "pass" for c in result["checks"].values()) else "fail"
    return result
