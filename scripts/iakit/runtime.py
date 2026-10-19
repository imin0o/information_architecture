"""配布物に同梱する実行時処理と知識（D-M4-10、P-M4-02、BR-03）。

配布先にリポジトリがなくても、run・input・contracts check・run judgment を使えるようにする。
入口 scripts/run_iakit.py と、そこから使うモジュール（import を辿って集める）、スキーマを、
リポジトリと同じ相対配置（scripts/iakit、schemas）で ia-orchestrator の下へ写す。
知識（索引・本文・用語・矛盾・根拠情報）は、各Skillが参照するKN-IDの分だけ写す（dist.build）。
"""

import ast
import re
import shutil
from pathlib import Path

from . import manifest, provenance, yamlio
from .cases import CASES
from .paths import EXTRACTED, ROOT, SCHEMAS

KNOWLEDGE_INDEX = "library/index.yaml"
TERMS = "library/terms.yaml"
CONFLICTS = "library/conflicts.yaml"
PACKAGE = ROOT / "scripts" / "iakit"
ENTRY = ROOT / "scripts" / "run_iakit.py"
CHECKER = ROOT / "scripts" / "check_artifacts.py"
SEEDS = ("cli_records", "cli_run", "contracts", "judgment")
SCHEMA_REF = re.compile(r'"\$ref":\s*"([a-z0-9-]+\.schema\.json)')


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


def schema_closure(names: set[str]) -> list[str]:
    """スキーマと、その $ref が指すスキーマ（配布物内で参照が閉じるようにする。D-M2-03）。"""
    seen, todo = set(), sorted(names)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += sorted(set(SCHEMA_REF.findall((SCHEMAS / name).read_text(encoding="utf-8"))) - seen)
    return sorted(seen)


def bundle(dest: Path) -> list[str]:
    """dest（ia-orchestrator）へ実行時処理とスキーマ、全知識（文献ノートを除く）を写し、相対パスを返す。"""
    return bundle_runtime(dest) + bundle_knowledge(dest, None)


def bundle_runtime(dest: Path) -> list[str]:
    written = []
    for name in modules():
        target = dest / "scripts" / "iakit" / f"{name}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / f"{name}.py", target)
        written.append(target.relative_to(dest).as_posix())
    shutil.copyfile(ENTRY, dest / "scripts" / ENTRY.name)
    written.append(f"scripts/{ENTRY.name}")
    written += bundle_schemas(dest, {p.name for p in SCHEMAS.glob("*.schema.json")})
    return written + _cases(dest)


def bundle_schemas(dest: Path, names: set[str]) -> list[str]:
    (dest / "schemas").mkdir(parents=True, exist_ok=True)
    written = []
    for name in schema_closure(names):
        shutil.copyfile(SCHEMAS / name, dest / "schemas" / name)
        written.append(f"schemas/{name}")
    return written


def bundle_checker(dest: Path) -> list[str]:
    """分野Skillを単独で使うときの形式検査（P-M6-01）。同梱スキーマだけを使い、iakit を要しない。"""
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CHECKER, dest / "scripts" / CHECKER.name)
    return [f"scripts/{CHECKER.name}"]


def bundle_knowledge(dest: Path, knowledge_ids: set[str] | None) -> list[str]:
    """索引・本文・用語・矛盾・根拠情報・出典ハッシュ。knowledge_ids が None なら文献ノート以外の全件。"""
    written = _knowledge(dest, knowledge_ids)
    written += _evidence(dest)
    sources = provenance.registered_sources()
    claims = yamlio.load(dest / provenance.RUNTIME_EVIDENCE)["claims"]
    # 同梱する主張の出典と自作素材だけを写す（無関係な文献の追加で配布物を書き換えない。BR-03）
    used = {c["source_id"] for c in claims} | {s for s in sources if s.startswith("FIXTURE-")}
    (dest / provenance.REGISTERED).write_text(
        "# 出典IDと原典のsha256（配布物の照合用。runtime.bundle_knowledge が生成）\n"
        + yamlio.dumps({"sources": {k: v for k, v in sources.items() if k in used},
                        "runtime_evidence_sha256": manifest.sha256_file(dest / provenance.RUNTIME_EVIDENCE)}),
        encoding="utf-8", newline="\n")
    return written + [provenance.REGISTERED]


def _evidence(dest: Path) -> list[str]:
    """構築時の照合状態を固定する。原典本文・抜粋・抽出ファイルのパスは同梱しない。"""
    prov, receipts = yamlio.load(ROOT / "library/provenance.yaml"), yamlio.load(ROOT / "verification/receipts.yaml")
    errors = (yamlio.schema_errors(prov, "provenance.schema.json")
              + yamlio.schema_errors(receipts, "verification-receipt.schema.json"))
    if not errors:
        errors = provenance.reference_errors(prov, receipts, provenance.registered_sources())
    if errors:
        raise ValueError(f"実行時の根拠情報を生成できない: {errors[:3]}")
    derived = provenance.derive(prov, receipts, ROOT, EXTRACTED)
    index = yamlio.load(dest / KNOWLEDGE_INDEX)
    wanted = {c for e in index["entries"] for c in e["claims"]}
    wanted.update(c for t in yamlio.load(dest / TERMS)["terms"] for c in t["claims"])
    wanted.update(p["claim_id"] for c in yamlio.load(dest / CONFLICTS)["conflicts"] for p in c["positions"])
    claims = [{"source_id": r["source_id"], "source_sha256": r["source_sha256"],
               "evidence_id": r["evidence_id"], "claim_id": c["claim_id"], "claim": c["claim"],
               "claim_sha256": c["claim_sha256"], "applicability": c["applicability"],
               **derived["states"][(r["evidence_id"], c["claim_id"])]}
              for r in prov["records"] for c in r["support_reviews"] if c["claim_id"] in wanted]
    evidence = {c["evidence_id"] for c in claims}
    doc = {
        "schema_version": "1.0",
        # 同梱する根拠に結び付いた照合記録だけを固定する（無関係な記録の追加で変わらない）
        "receipts": [{"receipt_id": rc["receipt_id"], "sha256": provenance.canonical_sha256(rc)}
                     for rc in receipts["receipts"] if rc["evidence_id"] in evidence],
        "knowledge_files": [{"path": e["path"], "sha256": manifest.sha256_file(dest / e["path"])}
                            for e in index["entries"]],
        "claims": claims,
    }
    missing = sorted(wanted - {c["claim_id"] for c in claims})
    if missing:
        raise ValueError(f"同梱する知識の主張が出典登録簿にない: {missing}")
    errors = yamlio.schema_errors(doc, "runtime-evidence.schema.json")
    if errors:
        raise ValueError(f"実行時の根拠情報がスキーマに合わない: {errors[:3]}")
    (dest / provenance.RUNTIME_EVIDENCE).write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    return [provenance.RUNTIME_EVIDENCE]


def _cases(dest: Path) -> list[str]:
    """run judgment が読む固定ケースとルーブリック。ケースの入力は写さない。"""
    written = []
    for src in [CASES / "rubric.yaml", *sorted(CASES.glob("*/case.yaml"))]:
        rel = src.relative_to(ROOT)
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / rel)
        written.append(rel.as_posix())
    return written


def _knowledge(dest: Path, knowledge_ids: set[str] | None) -> list[str]:
    """run knowledge が読む索引と知識本文、本文が使う用語と矛盾記録。文献ノート（book_note）は写さない（D-M4-06）。"""
    index = yamlio.load(ROOT / KNOWLEDGE_INDEX)
    entries = [e for e in index["entries"] if e["kind"] != "book_note"]
    if knowledge_ids is not None:
        missing = sorted(knowledge_ids - {e["knowledge_id"] for e in entries})
        if missing:
            raise ValueError(f"Skillが参照する知識が索引にない（または文献ノート）: {missing}")
        entries = [e for e in entries if e["knowledge_id"] in knowledge_ids]
    # needs（代表ケースの必要知識と候補文献）は構築時の管理情報なので写さない
    index = {"schema_version": index["schema_version"], "entries": entries, "needs": []}
    written = []
    for e in entries:
        (dest / e["path"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / e["path"], dest / e["path"])
        written.append(e["path"])
    (dest / KNOWLEDGE_INDEX).write_text(
        "# 配布物に同梱した知識の索引（runtime.bundle_knowledge が library/index.yaml から生成）\n"
        + yamlio.dumps(index), encoding="utf-8", newline="\n")
    terms = {t for e in entries for t in e["terms"]}
    conflicts = {c for e in entries for c in e["conflicts"]}
    for rel, key, wanted, id_key in ((TERMS, "terms", terms, "term_id"),
                                     (CONFLICTS, "conflicts", conflicts, "conflict_id")):
        doc = yamlio.load(ROOT / rel)
        doc[key] = [item for item in doc[key] if item[id_key] in wanted]
        (dest / rel).write_text("# 同梱した知識が参照する分だけを写した（runtime.bundle_knowledge が生成）\n"
                                + yamlio.dumps(doc), encoding="utf-8", newline="\n")
    return written + [KNOWLEDGE_INDEX, TERMS, CONFLICTS]
