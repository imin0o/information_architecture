"""dist/ の ia-* を個人用ルート（Claude Code・Codex）へ写し直す。

  uv run --locked python scripts/sync_global_skills.py            # dist build → 写し直し → dist check
  uv run --locked python scripts/sync_global_skills.py --hook     # Claude Code の PostToolUse から（stdin の JSON）
  uv run --locked python scripts/sync_global_skills.py --git-head # post-commit から（直前のコミットの変更）

--hook と --git-head は、配布物の元（skills/・library/・schemas/・scripts/）に変更がなければ何もしない。
ルートが存在しない場合は飛ばす。build-manifest.yaml が dist/ と同じで dist check も通るルートは写し直さない。
失敗時は終了コード2（Claude Code が stderr を受け取る）。
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SOURCES = ("skills/", "library/", "schemas/", "scripts/")
TARGETS = (Path.home() / ".claude" / "skills", Path.home() / ".agents" / "skills")
MANIFEST_NAME = "build-manifest.yaml"


def touches_source(paths: list[str]) -> bool:
    rel = []
    for p in paths:
        path = Path(p)
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(ROOT)
            except ValueError:
                continue
        rel.append(path.as_posix())
    return any(r.startswith(SOURCES) and not r.startswith("scripts/sync_global_skills") for r in rel)


def iakit(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", "--locked", "iakit", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8")


def matches_dist(target: Path) -> bool:
    """配置先の build-manifest.yaml が dist/ と同じか。dist check は配置先の内部整合しか見ないので、
    これと合わせて初めて配置先の全ファイルが dist/ と一致すると言える。"""
    for src in (p for p in DIST.iterdir() if p.is_dir()):
        placed = target / src.name / MANIFEST_NAME
        if not placed.is_file() or placed.read_bytes() != (src / MANIFEST_NAME).read_bytes():
            return False
    return True


def sync(target: Path) -> None:
    for src in sorted(p for p in DIST.iterdir() if p.is_dir()):
        dest, tmp = target / src.name, target / f".{src.name}.sync-new"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__"))
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="sync_global_skills")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--hook", action="store_true")
    mode.add_argument("--git-head", action="store_true")
    args = parser.parse_args(argv)
    if args.hook:
        tool_input = json.load(sys.stdin).get("tool_input", {})
        if not touches_source([tool_input.get("file_path", "")]):
            return 0
    elif args.git_head:
        out = subprocess.run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if not touches_source(out.stdout.splitlines()):
            return 0
    built = iakit("dist", "build")
    if built.returncode != 0:
        sys.stderr.write(built.stdout + built.stderr + "\ndist build が失敗したため、個人用ルートへは写していません。\n")
        return 2
    for target in TARGETS:
        if not target.is_dir():
            continue
        if matches_dist(target) and iakit("dist", "check", str(target)).returncode == 0:
            print(f"一致済み: {target}")
            continue
        sync(target)
        checked = iakit("dist", "check", str(target))
        if checked.returncode != 0:
            sys.stderr.write(checked.stdout + checked.stderr + f"\n写し直し後の dist check が失敗しました: {target}\n")
            return 2
        if not matches_dist(target):
            sys.stderr.write(f"写し直し後も build-manifest.yaml が dist/ と一致しません: {target}\n")
            return 2
        print(f"写し直し: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
