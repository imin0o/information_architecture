"""実行ディレクトリ単位のスキーマ・整合性検査（iakit contracts check）。

スキーマ検査に通ったファイルだけを整合性検査へ渡す。出典登録簿と照合記録は
ディレクトリ内にあればそれを、なければ library/provenance.yaml・verification/receipts.yaml を使う。
"""

from pathlib import Path

from . import decisions, manifest, provenance, provenance_local, run_outputs, runs, structure, yamlio
from .paths import CHECKOUT, EXTRACTED, FIXTURES, ORIGINALS, ROOT

SCHEMAS = {
    "inputs.yaml": "input.schema.json",
    "decisions.yaml": "decision.schema.json",
    "observations.yaml": "observation.schema.json",
    "run.yaml": "run.schema.json",
    "provenance.yaml": "provenance.schema.json",
    "receipts.yaml": "verification-receipt.schema.json",
    "content-inventory.yaml": "content-inventory.schema.json",
    "controlled-vocabulary.yaml": "controlled-vocabulary.schema.json",
    "taxonomy.yaml": "taxonomy.schema.json",
    "sitemap.yaml": "sitemap.schema.json",
    "build-manifest.yaml": "build-manifest.schema.json",
    "findings.yaml": "findings.schema.json",
    "runtime-evidence.yaml": "runtime-evidence.schema.json",
}
SHARED = {"provenance.yaml": ROOT / "library/provenance.yaml", "receipts.yaml": ROOT / "verification/receipts.yaml"}
EMPTY = {"provenance.yaml": {"schema_version": "1.0", "records": []},
         "receipts.yaml": {"schema_version": "1.0", "receipts": []}}
ARTIFACT_FILES = {
    "content_inventory": "content-inventory.yaml", "controlled_vocabulary": "controlled-vocabulary.yaml",
    "taxonomy": "taxonomy.yaml", "sitemap": "sitemap.yaml",
}


def default_targets() -> list[Path]:
    """自作の契約素材と、共有の出典登録簿（作成後）。"""
    targets = sorted(p for p in (FIXTURES / "contracts").iterdir() if p.is_dir())
    return targets + ([ROOT / "library"] if SHARED["provenance.yaml"].is_file() else [])


def load_bundle(directory: Path) -> tuple[dict, list[str]]:
    docs, errors = {}, []
    if not directory.is_dir():
        return docs, ["検査対象のディレクトリがない"]
    if not any((directory / name).is_file() for name in SCHEMAS):
        return docs, ["検査対象の契約ファイルがない"]

    def read(path, name):
        try:
            data = yamlio.load(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: 読込み失敗: {exc}")
            return
        found = yamlio.schema_errors(data, SCHEMAS[name])
        errors.extend(f"{name} {e}" for e in found)
        if not found:
            docs[name] = data

    for name in SCHEMAS:
        path = directory / name
        if not path.is_file() and name in SHARED:
            path = SHARED[name]
        if not path.is_file():
            continue
        read(path, name)
    if not CHECKOUT and "provenance.yaml" not in docs:
        read(ROOT / provenance.RUNTIME_EVIDENCE, "runtime-evidence.yaml")
    run = docs.get("run.yaml")
    if not (directory / "run.yaml").is_file() and any(
            name in docs for name in ("inputs.yaml", "decisions.yaml", "observations.yaml", *ARTIFACT_FILES.values())):
        errors.append("必須ファイル run.yaml がない")
    if run:
        for name in ("inputs.yaml", "decisions.yaml"):
            if not (directory / name).is_file():
                errors.append(f"run.yaml: 必須ファイル {name} がない")
        # 登録されたパスの実体を型に対応するスキーマで検査する。サブディレクトリも許容する。
        for a in run["artifacts"]:
            path = directory / a["path"]
            if not path.is_file():
                errors.append(f"{a['artifact_id']}: 成果物ファイル {a['path']} がない")
                continue
            name = ARTIFACT_FILES.get(a["kind"])
            if name and path.resolve() != (directory / name).resolve():
                if (directory / name).is_file():
                    errors.append(f"{name}: 同じ型の成果物が登録パス {a['path']} と重複している")
                else:
                    read(path, name)
    return docs, errors


def _originals() -> dict[str, Path]:
    found = {}
    for base in (ORIGINALS, FIXTURES):
        for path in (p for p in base.rglob("*") if p.is_file()) if base.is_dir() else ():
            if path.suffix.lower() in (".pdf", ".epub"):
                found[manifest.sha256_file(path)] = path
    return found


def _artifact_document_errors(run: dict, docs: dict) -> list[str]:
    errors = []
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    for kind, name in ARTIFACT_FILES.items():
        doc = docs.get(name)
        registered = [a for a in run["artifacts"] if a["kind"] == kind]
        if len(registered) > 1:
            errors.append(f"{name}: 同じ型の成果物が複数登録されている")
        if doc is None:
            if registered:
                errors.append(f"{name}: 登録された成果物のデータがない")
            continue
        a = arts.get(doc["artifact_id"])
        if a is None or a["kind"] != kind:
            errors.append(f"{name}: artifact_id と型の組が実行記録にない")
            continue
        for key, label in (("version", "版"), ("classification", "機密区分")):
            if a[key] != doc[key]:
                errors.append(f"{name}: {label}が実行記録の {a['artifact_id']} と一致しない")
        # 本体が参照する入力・成果物は、依存リストから省略させない。
        required = set()
        if kind == "content_inventory":
            required.update(i["input_id"] for i in doc["items"])
        elif kind == "controlled_vocabulary":
            required.update(s["input_id"] for c in doc["concepts"] for s in c["sources"])
        else:
            required.add(doc["inventory"]["artifact_id"])
            if kind == "taxonomy":
                required.add(doc["vocabulary"]["artifact_id"])
            else:
                if "taxonomy.yaml" in docs:
                    required.add(docs["taxonomy.yaml"]["artifact_id"])
                if any(n["concept_id"] for n in doc["nodes"]) and "controlled-vocabulary.yaml" in docs:
                    required.add(docs["controlled-vocabulary.yaml"]["artifact_id"])
        for ref in sorted(required - {dep["ref"] for dep in a["depends_on"]}):
            errors.append(f"{a['artifact_id']}: 本体が参照する依存 {ref} が depends_on にない")
    return errors


def consistency_errors(directory: Path, docs: dict, sources: dict,
                       extracted_root: Path = EXTRACTED) -> tuple[list[str], dict]:
    """スキーマに通ったファイル間の整合性。原典のない環境でも実行できる層だけを扱う。"""
    prov = docs.get("provenance.yaml", EMPTY["provenance.yaml"])
    receipts = docs.get("receipts.yaml", EMPTY["receipts.yaml"])
    errors = provenance.reference_errors(prov, receipts, sources)
    derived = provenance.derive(prov, receipts, ROOT, extracted_root)
    claims = provenance.claims_index(prov)
    if not CHECKOUT and "runtime-evidence.yaml" in docs and "provenance.yaml" not in docs:
        claims, derived["states"], found = provenance.runtime_claims(docs["runtime-evidence.yaml"], sources)
        errors += found
    inputs_doc = docs.get("inputs.yaml", {"run_id": None, "inputs": []})
    inputs = {i["input_id"]: i for i in inputs_doc["inputs"]}
    errors += runs.input_errors(inputs_doc)
    file_errors, derived["input_files"] = runs.input_file_results(inputs_doc, directory)
    errors += file_errors
    run = docs.get("run.yaml")
    arts = {a["artifact_id"]: a for a in run["artifacts"]} if run else {}
    decision_list = docs.get("decisions.yaml", {"decisions": []})["decisions"]
    studies, study_errors = decisions.study_index(docs.get("observations.yaml", {"studies": []})["studies"], inputs)
    errors += study_errors
    if "decisions.yaml" in docs:
        target_ids = set(arts)
        for name, collection, key in (("content-inventory.yaml", "items", "content_id"),
                                      ("controlled-vocabulary.yaml", "concepts", "concept_id"),
                                      ("taxonomy.yaml", "schemes", "scheme_id"),
                                      ("sitemap.yaml", "nodes", "node_id"),
                                      ("findings.yaml", "findings", "finding_id")):
            target_ids.update(item[key] for item in docs.get(name, {}).get(collection, []))
        versions = {a: v["version"] for a, v in arts.items() if v["status"] == "current"}
        errors += decisions.decision_errors(docs["decisions.yaml"], claims,
                                            derived["states"], inputs, studies, versions, target_ids)
    if run:
        errors += _artifact_document_errors(run, docs)
        actual = {a["artifact_id"]: manifest.sha256_file(directory / a["path"])
                  for a in run["artifacts"] if (directory / a["path"]).is_file()}
        inputs_path = directory / "inputs.yaml"
        inputs_sha = manifest.sha256_file(inputs_path) if inputs_path.is_file() else None
        errors += runs.run_errors(run, inputs_doc, inputs_sha, actual, decision_list, list(studies.values()))
        errors += runs.review_errors(run, runs.flagged_units(directory, inputs))
    inv, vocab = docs.get("content-inventory.yaml"), docs.get("controlled-vocabulary.yaml")
    if inv:
        errors += structure.inventory_errors(inv, inputs)
    if vocab:
        errors += structure.vocabulary_errors(vocab, inputs)
    if "taxonomy.yaml" in docs:
        errors += structure.taxonomy_errors(docs["taxonomy.yaml"], vocab, inv)
        active = decisions.latest_active(decision_list)
        errors += [f"taxonomy.yaml: {s['scheme_id']} が現行でない判断 {s['decision_ref']['decision_id']} を参照している"
                   for s in docs["taxonomy.yaml"]["schemes"]
                   if (s["decision_ref"]["decision_id"], s["decision_ref"]["decision_revision"]) not in active]
    if "sitemap.yaml" in docs:
        errors += structure.sitemap_errors(docs["sitemap.yaml"], inv, vocab)
    for name, doc in docs.items():
        if run and "run_id" in doc and doc["run_id"] != run["run_id"]:
            errors.append(f"{name}: run_id が実行記録と一致しない")
    errors += run_outputs.check(directory, docs)
    return errors, derived


def check_bundle(directory: Path, local: bool = False, extracted_root: Path = EXTRACTED) -> dict:
    docs, errors = load_bundle(directory)
    derived = {"stale_receipts": {}, "input_files": "skipped"}
    if not errors:
        found, derived = consistency_errors(directory, docs, provenance.registered_sources(), extracted_root)
        errors += found
    excerpt = "skipped"
    if local and not errors and "provenance.yaml" in docs:
        local_errs, excerpt = provenance_local.local_results(docs["provenance.yaml"], extracted_root, _originals())
        errors += local_errs
    try:
        shown = directory.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        shown = directory.name
    return {
        "check": "contracts", "target": shown, "result": "fail" if errors else "pass",
        "files": sorted(docs), "input_files": derived["input_files"], "local_excerpt_check": excerpt,
        # 原典・位置の照合は人の工程であり、この検査では再実行しない。既存の照合記録の対応だけを確かめる
        "human_source_check": "not_run",
        "existing_receipts": {"valid": len(docs.get("receipts.yaml", EMPTY["receipts.yaml"])["receipts"])
                              - len(derived["stale_receipts"]), "stale": sorted(derived["stale_receipts"])},
        "errors": errors,
    }
