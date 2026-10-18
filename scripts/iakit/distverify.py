"""配布物の検査（BR-03、AC-02、AC-10）。構築時（iakit dist check）と配布先の開始時（run init）で同じ処理を使う。

build-manifest.yaml の同梱ファイルのハッシュ・配布ID・契約版を照合し、欠落・追加・変更されたファイル、
依存Skillの不足や契約版の不一致、同じ共有ID（知識・主張・用語など）で内容の異なる配布物の混在を検出する。
"""

from pathlib import Path

from . import manifest, provenance, yamlio

MANIFEST_NAME = "build-manifest.yaml"
# 配布先で Python が作るキャッシュは同梱物として扱わない
IGNORED_PARTS = {"__pycache__"}


def frontmatter(skill_dir: Path) -> dict:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    return yamlio.loads(text.split("---", 2)[1]) if text.startswith("---") else {}


def files_of(skill_dir: Path) -> dict[str, str]:
    """build-manifest.yaml 以外の同梱ファイルと sha256。"""
    return {p.relative_to(skill_dir).as_posix(): manifest.sha256_file(p)
            for p in sorted(skill_dir.rglob("*"))
            if p.is_file() and p != skill_dir / MANIFEST_NAME
            and not IGNORED_PARTS & set(p.relative_to(skill_dir).parts)}


def distribution_id(doc: dict) -> str:
    """自分自身（ファイル）と配布IDを除いた確定内容から決める（BR-03）。"""
    return "DIST-" + provenance.canonical_sha256({k: v for k, v in doc.items() if k != "distribution_id"})[:16]


def skill_errors(skill_dir: Path) -> tuple[dict | None, list[str]]:
    name = skill_dir.name
    path = skill_dir / MANIFEST_NAME
    if not path.is_file():
        return None, [f"{name}: {MANIFEST_NAME} がない"]
    try:
        doc = yamlio.load(path)
    except (OSError, ValueError) as exc:
        return None, [f"{name}: {MANIFEST_NAME} の読込み失敗: {exc}"]
    errors = [f"{name}: {MANIFEST_NAME} {e}" for e in yamlio.schema_errors(doc, "build-manifest.schema.json")]
    if errors:
        return None, errors
    if doc["skill"]["name"] != name:
        errors.append(f"{name}: manifest のSkill名 {doc['skill']['name']} と配置名が異なる")
    if distribution_id(doc) != doc["distribution_id"]:
        errors.append(f"{name}: 配布ID {doc['distribution_id']} が manifest の内容と一致しない")
    meta = (frontmatter(skill_dir).get("metadata") or {}) if (skill_dir / "SKILL.md").is_file() else {}
    if str(meta.get("contract_version")) != doc["contract_version"] or str(meta.get("version")) != doc["skill"]["version"]:
        errors.append(f"{name}: SKILL.md の版・契約版が manifest と異なる")
    recorded = {f["path"]: f["sha256"] for f in doc["files"] + doc["schemas"]}
    actual = files_of(skill_dir)
    for rel in sorted(recorded.keys() - actual.keys()):
        errors.append(f"{name}: 同梱ファイル {rel} がない")
    for rel in sorted(actual.keys() - recorded.keys()):
        errors.append(f"{name}: manifest にないファイル {rel} がある（旧版の残りや別配布物との混在）")
    for rel in sorted(recorded.keys() & actual.keys()):
        if recorded[rel] != actual[rel]:
            errors.append(f"{name}: {rel} が配布時から変わった")
    evidence_path = skill_dir / provenance.RUNTIME_EVIDENCE
    if evidence_path.is_file():
        evidence = yamlio.load(evidence_path)
        found = yamlio.schema_errors(evidence, "runtime-evidence.schema.json")
        errors += [f"{name}: runtime-evidence.yaml {e}" for e in found]
        if not found:
            pending = [c["claim_id"] for c in evidence["claims"]
                       if (c["source_verification"], c["support_status"]) != ("verified", "supported")]
            if pending:
                errors.append(f"{name}: 有効な原典照合・主張レビューがない: {pending}")
    return doc, errors


def set_errors(skills_root: Path, names: list[str], require_all: bool = True) -> list[str]:
    """並べて配置したSkillの組の検査。manifest が1つもなければ開発用の写しとして検査しない。

    require_all が偽なら、names にないSkillへの依存は不足として扱わない（経路に要らないSkill）。
    """
    present = [n for n in names if (skills_root / n / "SKILL.md").is_file()]
    with_manifest = [n for n in names if (skills_root / n / MANIFEST_NAME).is_file()]
    if not with_manifest:
        return []
    errors = [f"{n}: {MANIFEST_NAME} がない（配布物と開発用の写しが混在している）"
              for n in present if n not in with_manifest]
    docs = {}
    for n in with_manifest:
        doc, found = skill_errors(skills_root / n)
        errors += found
        if doc:
            docs[n] = doc
    owners: dict[str, tuple[str, str]] = {}
    for n, doc in sorted(docs.items()):
        for req in doc["requires_skills"]:
            other = docs.get(req["name"])
            if other is None:
                if require_all or req["name"] in names:
                    errors.append(f"{n}: 依存Skill {req['name']} が配置されていない")
            elif other["contract_version"] != req["contract_version"]:
                errors.append(f"{n}: 依存Skill {req['name']} の契約版 {other['contract_version']} が"
                              f"必要な版 {req['contract_version']} と異なる")
        for item in doc["knowledge"]:
            first = owners.setdefault(item["id"], (n, item["sha256"]))
            if first[1] != item["sha256"]:
                errors.append(f"{item['id']}: {first[0]} と {n} で内容が異なる。両方を同じ配布から更新する")
    return errors
