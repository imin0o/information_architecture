"""配布物に同梱する実行時処理（D-M4-10、P-M4-02）。

配布先にリポジトリがなくても、run・input・contracts check・run judgment を使えるようにする。
入口 scripts/run_iakit.py と、そこから使うモジュール（import を辿って集める）、スキーマを、
リポジトリと同じ相対配置（scripts/iakit、schemas）で ia-orchestrator の下へ写す。
"""

import ast
import shutil
from pathlib import Path

from . import provenance, yamlio
from .cases import CASES
from .paths import ROOT, SCHEMAS

KNOWLEDGE_INDEX = "library/index.yaml"
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
    return written + _cases(dest) + _knowledge(dest)


def _cases(dest: Path) -> list[str]:
    """run judgment が読む固定ケースとルーブリック。ケースの入力は写さない。"""
    written = []
    for src in [CASES / "rubric.yaml", *sorted(CASES.glob("*/case.yaml"))]:
        rel = src.relative_to(ROOT)
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / rel)
        written.append(rel.as_posix())
    return written


def _knowledge(dest: Path) -> list[str]:
    """run knowledge が読む索引と知識本文。Skillが参照しない文献ノート（book_note）は写さない（D-M4-06）。

    引用量の上限や公開範囲の確認は、配布生成（M5）が引用台帳で行う。
    """
    index = yamlio.load(ROOT / KNOWLEDGE_INDEX)
    index["entries"] = [e for e in index["entries"] if e["kind"] != "book_note"]
    written = []
    for e in index["entries"]:
        (dest / e["path"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / e["path"], dest / e["path"])
        written.append(e["path"])
    (dest / KNOWLEDGE_INDEX).write_text(
        "# 配布物に同梱した知識の索引（runtime.bundle が library/index.yaml から文献ノートを除いて生成）\n"
        + yamlio.dumps(index), encoding="utf-8")
    return written + [KNOWLEDGE_INDEX]
