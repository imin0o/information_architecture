"""配布物の生成・検査・差分（iakit dist build | check | diff）。BR-03、AC-02、付録B.3。

各Skillへ、SKILL.md と付属ファイル、参照する知識（索引・本文・用語・矛盾・根拠情報）、スキーマを同梱する。
処理順は、同梱ファイルの確定 → 対象に結び付いた照合記録（verification/bundle-receipt.yaml）→
最終manifest・配布ID。manifest 自身と配布IDはハッシュの入力に含めない。
新しい作業ディレクトリで生成・検査してから出力先をディレクトリ単位で入れ替え、旧ファイルを残さない。
"""

import re
import shutil
import tempfile
from pathlib import Path

from . import __version__, contracts, distverify, leaks, library, provenance, run_cmd, runs, runtime, yamlio
from .paths import ROOT, SKILLS

DIST = ROOT / "dist"
RECORD = ROOT / "verification" / "distributions.yaml"
RECEIPT = "verification/bundle-receipt.yaml"
ORCHESTRATOR = "ia-orchestrator"
KN_REF = re.compile(r"\bKN-[0-9]{3}\b")
YAML_NAME = re.compile(r"\b([a-z-]+\.yaml)\b")
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def skill_names() -> list[str]:
    return sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def _texts(skill_dir: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(skill_dir.rglob("*.md")))


def requires(name: str) -> list[dict]:
    """手順上の依存。ia-orchestrator は標準経路（MVPで対応する経路）の全段階のSkillを要する。"""
    if name != ORCHESTRATOR:
        return []
    needed = sorted({run_cmd.STAGE_SKILLS[s] for stages in runs.ROUTES.values() for s in stages})
    return [{"name": n, "contract_version": str(distverify.frontmatter(SKILLS / n)["metadata"]["contract_version"])}
            for n in needed]


def build_skill(name: str, dest: Path) -> dict:
    src = SKILLS / name
    shutil.copytree(src, dest)
    text = _texts(src)
    if name == ORCHESTRATOR:
        # 実行時処理は全経路の知識を読むため、文献ノート以外の全知識と全スキーマを同梱する
        runtime.bundle_runtime(dest)
        runtime.bundle_knowledge(dest, None)
    else:
        schemas = {contracts.SCHEMAS[n] for n in YAML_NAME.findall(text) if n in contracts.SCHEMAS}
        if schemas:
            runtime.bundle_schemas(dest, schemas)
            runtime.bundle_checker(dest)
        runtime.bundle_knowledge(dest, set(KN_REF.findall(text)))
    broken = _broken_links(dest)
    if broken:
        raise ValueError(f"{name}: 配布物内で解決できない参照: {broken[:5]}")
    _write(dest / RECEIPT, _receipt(name, dest), "配布物に結び付いた照合記録（本文を含まない。iakit dist build が生成）")
    doc = _manifest(name, dest)
    _write(dest / distverify.MANIFEST_NAME, doc, "配布manifest（iakit dist build が生成。この記録自身はハッシュに含めない）")
    return doc


def _write(path: Path, doc: dict, comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False,
                                         prefix=f".{path.name}-", dir=path.parent) as stream:
            staged = Path(stream.name)
            stream.write(f"# {comment}\n" + yamlio.dumps(doc))
        staged.replace(path)
    finally:
        if staged:
            staged.unlink(missing_ok=True)


def _broken_links(dest: Path) -> list[str]:
    """Skillルート基準の相対リンクが配布物の中で解決すること（ビルド元への参照を残さない）。"""
    broken = []
    for md in sorted(dest.rglob("*.md")):
        for target in LINK.findall(md.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            resolved = (md.parent / target).resolve()
            if not resolved.is_relative_to(dest.resolve()) or not resolved.exists():
                broken.append(f"{md.relative_to(dest).as_posix()} -> {target}")
    return broken


def _receipt(name: str, dest: Path) -> dict:
    """同梱ファイルを確定した後に作る。原典の再照合は行わず、既存の照合記録から導いた状態を数える。"""
    evidence = yamlio.load(dest / provenance.RUNTIME_EVIDENCE)
    claims = evidence["claims"]
    confirmed = [c for c in claims if (c["source_verification"], c["support_status"]) == ("verified", "supported")]
    pending = sorted(c["claim_id"] for c in claims if c not in confirmed)
    if pending:
        raise ValueError(f"{name}: 有効な原典照合・主張レビューがない: {pending}")
    files = distverify.files_of(dest)
    files.pop(RECEIPT, None)
    return {
        "schema_version": "1.0", "skill": name,
        "files": [{"path": p, "sha256": h} for p, h in files.items()],
        "verification_receipts": [r["receipt_id"] for r in evidence["receipts"]],
        "claims": {"total": len(claims), "verified_supported": len(confirmed),
                   "other": sorted(c["claim_id"] for c in claims if c not in confirmed)},
        "machine_checks": {"references": "pass", "runtime_evidence": "pass", "leaks": "pass"},
        "human_source_check": "existing_records" if claims and len(confirmed) == len(claims) else "partial",
    }


def _manifest(name: str, dest: Path) -> dict:
    meta = distverify.frontmatter(dest)["metadata"]
    evidence = yamlio.load(dest / provenance.RUNTIME_EVIDENCE)
    index = yamlio.load(dest / runtime.KNOWLEDGE_INDEX)
    prov = {r["evidence_id"]: r for r in yamlio.load(ROOT / "library/provenance.yaml")["records"]}
    knowledge = {e["knowledge_id"]: distverify.files_of(dest)[e["path"]] for e in index["entries"]}
    for c in evidence["claims"]:
        knowledge[c["claim_id"]] = provenance.canonical_sha256(c)
        knowledge[c["evidence_id"]] = provenance.record_sha256(prov[c["evidence_id"]])
    for rel, key, id_key in ((runtime.TERMS, "terms", "term_id"), (runtime.CONFLICTS, "conflicts", "conflict_id")):
        for item in yamlio.load(dest / rel)[key]:
            knowledge[item[id_key]] = provenance.canonical_sha256(item)
    files = distverify.files_of(dest)
    doc = {
        "schema_version": "1.0",
        "skill": {"name": name, "version": str(meta["version"])},
        "contract_version": str(meta["contract_version"]),
        "requires_skills": requires(name),
        "knowledge": [{"id": k, "sha256": v} for k, v in sorted(knowledge.items())],
        "schemas": [{"path": p, "sha256": h} for p, h in files.items() if p.startswith("schemas/")],
        "files": [{"path": p, "sha256": h} for p, h in files.items() if not p.startswith("schemas/")],
        "verification_receipts": [r["receipt_id"] for r in evidence["receipts"]],
        "tool": {"name": "iakit", "version": __version__},
    }
    doc["distribution_id"] = distverify.distribution_id(doc)
    errors = yamlio.schema_errors(doc, "build-manifest.schema.json")
    if errors:
        raise ValueError(f"{name}: manifest がスキーマに合わない: {errors[:3]}")
    return doc


def verify(root: Path, placed: bool = False) -> list[str]:
    """配布ディレクトリの検査。Skillの組の整合と、原典・機密データの混入。

    placed が真なら配置先（他のSkillと共有するフォルダ）として、正本の配布対象名のフォルダだけを検査する。
    依存先は正本の名前に含まれるため、欠落は set_errors が検出する。偽なら生成先の全フォルダを検査する。
    """
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if placed:
        names = sorted(set(names) & set(skill_names()))
    if not names:
        return [f"{root} に配布物がない"]
    errors = distverify.set_errors(root, names) or (
        [] if all((root / n / distverify.MANIFEST_NAME).is_file() for n in names) else ["build-manifest.yaml がない"])
    leak = leaks.check(dirs=[root / n for n in names] if placed else [root])
    errors += [f"混入: {v['path']}: {v['reason']}" for v in leak["violations"]]
    errors += [f"混入検査: {e['reason']}" for e in leak["errors"]]
    return errors


def _generate(work: Path) -> list[dict]:
    lib = library.check()
    if lib["result"] != "pass" or lib.get("knowledge", {}).get("not_supported"):
        raise ValueError(f"知識ライブラリの検査に通らない: {lib['errors'][:3] or lib.get('knowledge')}")
    bundled = {e["knowledge_id"] for e in yamlio.load(ROOT / runtime.KNOWLEDGE_INDEX)["entries"]
               if e["kind"] != "book_note"}
    pending = sorted(bundled - set(lib["knowledge"]["adopted"]))
    if pending:
        raise ValueError(f"配布する知識に有効な照合・レビュー記録がない: {pending}")
    return [build_skill(n, work / n) for n in skill_names()]


def _summary(docs: list[dict], root: Path) -> list[dict]:
    return [{"skill": d["skill"]["name"], "distribution_id": d["distribution_id"],
             "bundle_receipt_sha256": distverify.files_of(root / d["skill"]["name"])[RECEIPT],
             "files": len(d["files"]) + len(d["schemas"])} for d in docs]


def build(out: Path = DIST, record: Path | None = RECORD) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{out.name}-new-", dir=out.parent))
    try:
        docs = _generate(work)
        errors = verify(work)
        if errors:
            return {"check": "dist_build", "result": "fail", "errors": errors}
        summary = _summary(docs, work)
        changes = diff(out, work) if out.is_dir() else {"new": True}
        backup = Path(tempfile.mkdtemp(prefix=f".{out.name}-old-", dir=out.parent)).resolve() if out.exists() else None
        old = backup / "previous" if backup else None
        try:
            if old:
                out.rename(old)
            try:
                work.rename(out)
                if record:
                    _write(record, {"schema_version": "1.0", "distributions": summary},
                           "配布候補と、その同梱物に結び付いた照合記録のハッシュ（iakit dist build が生成。本文を含まない）")
            except OSError:
                try:
                    if not work.exists():
                        out.rename(work)
                    if old:
                        old.rename(out)
                except OSError as exc:
                    raise OSError(f"配布物の復元に失敗した。退避先 {old} と配置先 {out} を確認する") from exc
                raise
        finally:
            # 復元にも失敗した場合は旧配布物を消さず、場所をエラーに残す。
            if backup and not old.exists():
                backup.rmdir()
        if backup:
            shutil.rmtree(backup)
        return {"check": "dist_build", "result": "pass", "out": out.as_posix(), "distributions": summary,
                "diff": changes}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(root: Path | None = None, record: Path = RECORD) -> dict:
    """root を指定すれば配置済みの配布物を検査する。省略時は一時ディレクトリで再生成し、記録と照合する。"""
    result = {"check": "dist", "human_source_check": "not_run"}
    if root is not None:
        errors = verify(root, placed=True)
        return result | {"target": root.as_posix(), "result": "fail" if errors else "pass", "errors": errors}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            docs = _generate(Path(tmp))
        except ValueError as exc:
            return result | {"result": "fail", "errors": [str(exc)]}
        errors = verify(Path(tmp))
        summary = _summary(docs, Path(tmp))
    recorded = yamlio.load(record)["distributions"] if record.is_file() else None
    if recorded != summary:
        errors.append(f"{record.name} が現行の配布候補と一致しない。iakit dist build で再生成する")
    existing = sum(1 for d in docs for _ in d["verification_receipts"])
    return result | {"target": "regenerated", "result": "fail" if errors else "pass", "errors": errors,
                     "existing_receipts_checked": existing, "distributions": summary}


def diff(old: Path, new: Path) -> dict:
    """Skillごとの配布IDと、追加・削除・変更されたファイル。"""
    names = sorted({p.name for root in (old, new) if root.is_dir() for p in root.iterdir() if p.is_dir()})
    out = {}
    for n in names:
        a = distverify.files_of(old / n) if (old / n).is_dir() else {}
        b = distverify.files_of(new / n) if (new / n).is_dir() else {}
        ids = [yamlio.load(r / n / distverify.MANIFEST_NAME)["distribution_id"]
               if (r / n / distverify.MANIFEST_NAME).is_file() else None for r in (old, new)]
        changed = sorted(p for p in a.keys() & b.keys() if a[p] != b[p])
        out[n] = {"status": "unchanged" if ids[0] == ids[1] and a == b else "changed",
                  "distribution_id": ids, "added": sorted(b.keys() - a.keys()),
                  "removed": sorted(a.keys() - b.keys()), "changed": changed}
    return out
