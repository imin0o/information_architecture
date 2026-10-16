"""pre-pushが受け取った送信予定コミットを、削除前の履歴も含めて検査する。"""

import re
import subprocess
from pathlib import Path

from . import leak_sources, leaks, yamlio
from .paths import ROOT

OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def check_push(updates: str, remote: str, root: Path = ROOT) -> dict:
    results, errors, visited = [], [], set()
    try:
        for line in updates.splitlines():
            fields = line.split()
            if len(fields) != 4 or not all(OID.fullmatch(fields[i]) for i in (1, 3)):
                raise yamlio.DataError("pre-pushの参照情報が不正")
            _, local_oid, _, remote_oid = fields
            if set(local_oid) == {"0"}:  # リモート参照の削除には送信対象がない
                continue
            tip = leak_sources.git(root, "rev-parse", "--verify", local_oid + "^{commit}").decode().strip()
            baseline = leak_sources.policies(lambda path: leak_sources.read_git(root, path, tip), historical=True)
            if set(remote_oid) == {"0"}:
                args = ("rev-list", tip, "--not", f"--remotes={remote}")
            else:
                args = ("rev-list", tip, "--not", remote_oid)
            revisions = leak_sources.git(root, *args).decode().splitlines()
            # 先端は必ず確認し、古い履歴の登録簿欠落は先端の原典一覧で補う。
            revisions = [rev for rev in dict.fromkeys([tip, *revisions]) if (tip, rev) not in visited]
            visited.update((tip, rev) for rev in revisions)
            results.append(leaks.check(root=root, revisions=revisions, baseline=baseline))
    except (ValueError, OSError, subprocess.CalledProcessError):
        errors.append("送信予定コミットまたは判定用登録簿を確認できないため停止")
    return {
        "check": "leaks:pre-push",
        "result": "fail" if errors or any(r["result"] == "fail" for r in results) else "pass",
        "commits_scanned": len(visited), "errors": errors, "checks": results,
    }
