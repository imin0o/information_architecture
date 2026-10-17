"""配布物に同梱する実行時処理（D-M4-10、P-M4-02）。

配布先にリポジトリがなくても、run・input・contracts check・run judgment を使えるようにする。
入口 scripts/run_iakit.py と、そこから使うモジュール（import を辿って集める）、スキーマを、
リポジトリと同じ相対配置（scripts/iakit、schemas）で ia-orchestrator の下へ写す。
"""

import ast
import shutil
from pathlib import Path

from . import provenance, yamlio
from .paths import ROOT, SCHEMAS

PACKAGE = ROOT / "scripts" / "iakit"
ENTRY = ROOT / "scripts" / "run_iakit.py"
SEEDS = ("cli_run", "contracts", "judgment")


def _imports(name: str) -> set[str]:
    tree = ast.parse((PACKAGE / f"{name}.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            found |= {a.name for a in node.names} if node.module is None else {node.module.split(".")[0]}
    return found


def modules() -> list[str]:
    """入口から辿れるパッケージ内モジュール。"""
    seen, todo = set(), ["__init__", "paths", *SEEDS]
    while todo:
        name = todo.pop()
        if name in seen or not (PACKAGE / f"{name}.py").is_file():
            continue
        seen.add(name)
        todo += sorted(_imports(name) - seen)
    return sorted(seen)


def bundle(dest: Path) -> list[str]:
    """dest（配布物の ia-orchestrator）へ実行時処理とスキーマを写し、写した相対パスを返す。"""
    written = []
    for name in modules():
        target = dest / "scripts" / "iakit" / f"{name}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / f"{name}.py", target)
        written.append(target.relative_to(dest).as_posix())
    (dest / "schemas").mkdir(parents=True, exist_ok=True)
    for schema in sorted(SCHEMAS.glob("*.schema.json")):
        shutil.copyfile(schema, dest / "schemas" / schema.name)
        written.append(f"schemas/{schema.name}")
    shutil.copyfile(ENTRY, dest / "scripts" / ENTRY.name)
    written.append(f"scripts/{ENTRY.name}")
    # 原典の登録簿は同梱せず、出典IDとハッシュだけを写す（本文・パスを含めない）
    (dest / provenance.REGISTERED).write_text(
        "# 出典IDと原典のsha256（配布物の照合用。runtime.bundle が生成）\n"
        + yamlio.dumps({"sources": provenance.registered_sources()}), encoding="utf-8")
    written.append(provenance.REGISTERED)
    return written
