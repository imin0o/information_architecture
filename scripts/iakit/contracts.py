"""実行ディレクトリ単位のスキーマ・整合性検査（iakit contracts check）。

スキーマ検査に通ったファイルだけを整合性検査へ渡す。出典登録簿と照合記録は
ディレクトリ内にあればそれを、なければ library/provenance.yaml・verification/receipts.yaml を使う。
"""

from pathlib import Path

from . import decisions, manifest, provenance, provenance_local, runs, structure, yamlio
from .paths import EXTRACTED, FIXTURES, ORIGINALS, ROOT

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
}
SHARED = {"provenance.yaml": ROOT / "library/provenance.yaml", "receipts.yaml": ROOT / "verification/receipts.yaml"}
EMPTY = {"provenance.yaml": {"schema_version": "1.0", "records": []},
         "receipts.yaml": {"schema_version": "1.0", "receipts": []}}


def default_targets() -> list[Path]:
    """自作の契約素材と、共有の出典登録簿（作成後）。"""
    targets = sorted(p for p in (FIXTURES / "contracts").iterdir() if p.is_dir())
    return targets + ([ROOT / "library"] if SHARED["provenance.yaml"].is_file() else [])


def load_bundle(directory: Path) -> tuple[dict, list[str]]:
    docs, errors = {}, []
    for name, schema in SCHEMAS.items():
        path = directory / name
        if not path.is_file() and name in SHARED:
            path = SHARED[name]
        if not path.is_file():
            continue
        try:
            data = yamlio.load(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: 読込み失敗: {exc}")
            continue
        found = yamlio.schema_errors(data, schema)
        errors += [f"{name} {e}" for e in found]
        if not found:
            docs[name] = data
    return docs, errors


def _originals() -> dict[str, Path]:
    found = {}
    for base in (ORIGINALS, FIXTURES):
        for path in (p for p in base.rglob("*") if p.is_file()) if base.is_dir() else ():
            if path.suffix.lower() in (".pdf", ".epub"):
                found[manifest.sha256_file(path)] = path
    return found


def consistency_errors(directory: Path, docs: dict, sources: dict) -> tuple[list[str], dict]:
    """スキーマに通ったファイル間の整合性。原典のない環境でも実行できる層だけを扱う。"""
    prov = docs.get("provenance.yaml", EMPTY["provenance.yaml"])
    receipts = docs.get("receipts.yaml", EMPTY["receipts.yaml"])
    errors = provenance.reference_errors(prov, receipts, sources)
    derived = provenance.derive(prov, receipts, ROOT)
    inputs_doc = docs.get("inputs.yaml", {"run_id": None, "inputs": []})
    inputs = {i["input_id"]: i for i in inputs_doc["inputs"]}
    errors += runs.input_errors(inputs_doc)
    file_errors, derived["input_files"] = runs.input_file_results(inputs_doc, directory)
    errors += file_errors
    run = docs.get("run.yaml")
    arts = {a["artifact_id"]: a for a in run["artifacts"]} if run else {}
    decision_list = docs.get("decisions.yaml", {"decisions": []})["decisions"]
    if "decisions.yaml" in docs:
        studies = {s["study_id"]: s for s in docs.get("observations.yaml", {"studies": []})["studies"]}
        versions = {a: v["version"] for a, v in arts.items() if v["status"] == "current"}
        errors += decisions.decision_errors(docs["decisions.yaml"], provenance.claims_index(prov),
                                            derived["states"], inputs, studies, versions)
    if run:
        actual = {a["artifact_id"]: manifest.sha256_file(directory / a["path"])
                  for a in run["artifacts"] if (directory / a["path"]).is_file()}
        inputs_path = directory / "inputs.yaml"
        inputs_sha = manifest.sha256_file(inputs_path) if inputs_path.is_file() else None
        errors += runs.run_errors(run, inputs_doc, inputs_sha, actual, decision_list)
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
        if "artifact_id" in doc and doc["artifact_id"] in arts and arts[doc["artifact_id"]]["version"] != doc["version"]:
            errors.append(f"{name}: 版が実行記録の {doc['artifact_id']} と一致しない")
    return errors, derived


def check_bundle(directory: Path, local: bool = False, extracted_root: Path = EXTRACTED) -> dict:
    docs, errors = load_bundle(directory)
    derived = {"stale_receipts": {}, "input_files": "skipped"}
    if not errors:
        found, derived = consistency_errors(directory, docs, provenance.registered_sources())
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
