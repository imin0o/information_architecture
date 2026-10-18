"""送信予定コミットの全件検査。作業ツリー・index・実行環境を一時checkoutから分離する。"""

import os
import subprocess
import tempfile
from pathlib import Path

from . import leak_history, yamlio
from .paths import ROOT


def _check_tip(root: Path, tip: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="iakit-push-") as temporary:
        scratch = Path(temporary).resolve()
        checkout = scratch / "checkout"
        env = dict(os.environ, PYTHONUTF8="1", UV_PROJECT_ENVIRONMENT=str(scratch / "venv"))
        for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "VIRTUAL_ENV"):
            env.pop(key, None)
        # shared cloneは原典や未コミットの変更を写さず、元のindexとworktree登録簿も変更しない。
        git = ["git", "-c", f"core.hooksPath={scratch / 'no-hooks'}"]
        subprocess.run([*git, "clone", "--shared", "--no-checkout", "--", str(root.resolve()), str(checkout)],
                       env=env, capture_output=True, check=True)
        subprocess.run([*git, "-C", str(checkout), "checkout", "--detach", tip],
                       env=env, capture_output=True, check=True)
        done = subprocess.run(["uv", "run", "--locked", "iakit", "check", "--local"],
                              cwd=checkout, env=env, capture_output=True)
        report = yamlio.loads(done.stdout.decode("utf-8"))
        if not isinstance(report, dict) or report.get("result") not in ("pass", "fail") or not report.get("checks"):
            raise ValueError(f"{tip}: 全件検査の結果を確認できない")
        return {"commit": tip, "result": "pass" if done.returncode == 0 and report["result"] == "pass" else "fail",
                "checks": report["checks"]}


def check_push(updates: str, root: Path = ROOT) -> dict:
    checks, errors, tips = [], [], []
    try:
        # 全行を検査してから実行する。参照削除は対象外、同じコミットは1回だけ調べる。
        for line in updates.splitlines():
            fields = line.split()
            if len(fields) != 4 or not all(leak_history.OID.fullmatch(fields[i]) for i in (1, 3)):
                raise ValueError("pre-pushの参照情報が不正")
            if set(fields[1]) == {"0"}:
                continue
            tip = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", fields[1] + "^{commit}"],
                                 capture_output=True, check=True).stdout.decode().strip()
            if tip not in tips:
                tips.append(tip)
        for tip in tips:
            checks.append(_check_tip(root, tip))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        errors.append(f"送信予定コミットの全件検査を完了できないため停止: {exc}")
    return {"check": "check:pre-push", "result": "fail" if errors or any(c["result"] == "fail" for c in checks) else "pass",
            "commits_checked": len(checks), "errors": errors, "checks": checks}
