"""index・コミットからファイルと判定用登録簿を同じ状態で読む。"""

import subprocess
from pathlib import Path

from . import fixtures, manifest, yamlio

MANIFEST_PATH = "sources/manifest.yaml"
FIXTURE_PATH = "tests/fixtures/fixtures.yaml"


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def read_git(root: Path, path: str, revision: str | None = None) -> bytes:
    return git(root, "show", f"{revision or ''}:{path}")


def git_paths(root: Path, staged_only: bool = False, revision: str | None = None) -> list[str]:
    if revision:
        out = git(root, "ls-tree", "-r", "--name-only", "-z", revision)
    elif staged_only:
        out = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        out = git(root, "ls-files", "-z", "--cached")
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def policies(read) -> tuple[set[str], dict[str, str]]:
    """欠落・不正な登録簿は空の許可リストとして扱わず停止する。"""
    try:
        data = yamlio.loads(read(MANIFEST_PATH).decode("utf-8"))
        if yamlio.schema_errors(data, "manifest.schema.json") or manifest.consistency_errors(data):
            raise ValueError("manifest validation")
        index = yamlio.loads(read(FIXTURE_PATH).decode("utf-8"))
        _, expected_index = fixtures.render()
        if index != yamlio.loads(expected_index):
            raise ValueError("fixture validation")
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError, RecursionError) as exc:
        raise yamlio.DataError("判定用登録簿が欠落・不正、または自作素材の生成器と不一致") from exc
    return manifest.known_hashes(data), {
        f"tests/fixtures/{entry['path']}": entry["sha256"] for entry in index["files"]
    }
