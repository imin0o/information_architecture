"""配布物に同梱する実行時処理が、リポジトリなしで動くこと（D-M4-10、P-M4-02）。"""

import shutil
import subprocess
import sys
from copy import deepcopy

import pytest

from iakit import provenance, runtime, yamlio
from iakit.paths import ROOT

BUNDLE = ROOT / "tests" / "fixtures" / "contracts" / "webia-run"


@pytest.fixture
def installed(tmp_path):
    """Skillを1つの置き場へ並べ、ia-orchestrator に実行時処理を同梱した配布先。"""
    skills = tmp_path / "installed skills"
    for skill in (ROOT / "skills").iterdir():
        shutil.copytree(skill, skills / skill.name)
    runtime.bundle(skills / "ia-orchestrator")
    return skills


def run(installed, *args):
    entry = installed / "ia-orchestrator" / "scripts" / "run_iakit.py"
    done = subprocess.run([sys.executable, "-I", str(entry), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", cwd=installed.parent)
    return done.returncode, yamlio.loads(done.stdout) if done.stdout else {"stderr": done.stderr}


def test_modules_cover_runtime_imports():
    names = runtime.modules()
    assert {"cli_run", "contracts", "judgment", "run_cmd", "run_inputs", "ingest", "ingest_docs", "yamlio"} <= set(names)
    assert not {"cli", "extract_cmd", "review", "leaks"} & set(names)


def test_bundled_runtime_uses_bundled_package(installed):
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import iakit.paths as p; "
             "print(p.ROOT.name, p.CHECKOUT, p.SKILLS.name)")
    done = subprocess.run([sys.executable, "-I", "-c", probe, str(installed / "ia-orchestrator" / "scripts")],
                          capture_output=True, text=True, check=True)
    assert done.stdout.strip() == f"ia-orchestrator False {installed.name}"


def test_bundled_init_records_installed_skills(installed, tmp_path):
    code, out = run(installed, "run", "init", tmp_path / "RUN-X-001", "--run-id", "RUN-X-001",
                    "--task-type", "taxonomy", "--host", "codex", "--model", "m")
    assert code == 0, out
    assert out["supported"] is True and out["missing_skills"] == []
    names = [s["name"] for s in yamlio.load(tmp_path / "RUN-X-001" / "run.yaml")["skills"]]
    assert names == ["ia-orchestrator", "ia-analysis", "ia-taxonomy", "ia-evaluation"]


def test_bundled_contracts_check_passes_fixture(installed, tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "run")
    code, out = run(installed, "contracts", "check", tmp_path / "run")
    assert (code, out["result"]) == (0, "pass"), out


def test_bundled_knowledge_records_hash_without_book_notes(installed, tmp_path):
    run(installed, "run", "init", tmp_path / "RUN-X-003", "--run-id", "RUN-X-003",
        "--task-type", "taxonomy", "--host", "codex", "--model", "m")
    code, out = run(installed, "run", "knowledge", tmp_path / "RUN-X-003", "KN-002")
    assert code == 0, out
    code, out = run(installed, "run", "knowledge", tmp_path / "RUN-X-003", "KN-001")
    assert code == 1 and "索引にない知識" in out["error"]


def test_bundled_judgment_reads_bundled_case(installed, tmp_path):
    from test_judgment import record
    shutil.copytree(BUNDLE, tmp_path / "run")
    data = record(yamlio.load(tmp_path / "run" / "run.yaml"))
    (tmp_path / "run" / "rubric-judgment.yaml").write_text(yamlio.dumps(data), encoding="utf-8")
    code, out = run(installed, "run", "judgment", tmp_path / "run", "--case", "webia-001")
    assert (code, out["result"]) == (0, "pass"), out


def test_bundled_runtime_reports_missing_skill(installed, tmp_path):
    shutil.rmtree(installed / "ia-taxonomy")
    code, out = run(installed, "run", "init", tmp_path / "RUN-X-002", "--run-id", "RUN-X-002",
                    "--task-type", "taxonomy", "--host", "codex", "--model", "m")
    assert code == 1 and "ia-taxonomy" in out["error"]


@pytest.fixture
def sourced_run(installed, tmp_path):
    directory = tmp_path / "sourced run"
    code, out = run(installed, "run", "init", directory, "--run-id", "RUN-SOURCE", "--task-type", "taxonomy",
                    "--host", "codex", "--model", "m")
    assert code == 0, out
    brief = tmp_path / "brief.txt"
    brief.write_text("An observation for a synthetic case.\n", encoding="utf-8")
    assert run(installed, "input", "add", directory, brief, "--kind", "brief", "--description", "synthetic brief")[0] == 0
    evidence = yamlio.load(installed / "ia-orchestrator" / provenance.RUNTIME_EVIDENCE)
    c = next(c for c in evidence["claims"] if c["source_verification"] == "verified" and c["support_status"] == "supported")
    decision = deepcopy(yamlio.load(BUNDLE / "decisions.yaml")["decisions"][0])
    decision.update(run_id="RUN-SOURCE", status="active", target_ids=["ART-001"], assumptions=[], confidence="medium",
                    input_observations=[{"observation_id": "OBS-001", "input_id": "INPUT-001", "locator": "brief.txt#L1",
                                         "observation": "An observation for a synthetic case."}],
                    sources=[{k: c[k] for k in ("source_id", "evidence_id", "claim_id", "claim",
                                                "source_verification", "support_status")} | {"primary": True}])
    (directory / "decisions.yaml").write_text(yamlio.dumps({"schema_version": "1.0", "run_id": "RUN-SOURCE",
                                                           "decisions": [decision]}), encoding="utf-8")
    (directory / "information-audit.md").write_text("# Audit\n\n## 仮定に基づく箇所\n\nなし\n\n## 判断\n\nIA-001 r1\n",
                                                    encoding="utf-8")
    code, out = run(installed, "run", "artifact", directory, "information-audit.md", "--kind", "information_audit")
    assert code == 0, out
    return directory


def test_bundled_runtime_checks_book_claim_without_repository_metadata(installed, sourced_run):
    assert not (sourced_run / "provenance.yaml").exists()
    assert not (sourced_run / "receipts.yaml").exists()
    code, out = run(installed, "contracts", "check", sourced_run)
    assert (code, out["result"]) == (0, "pass"), out
    text = (installed / "ia-orchestrator" / provenance.RUNTIME_EVIDENCE).read_text(encoding="utf-8")
    assert all(s not in text for s in ("excerpt_preview", "extracted_ref", "sources/originals", "sources/extracted"))
    assert not (installed / "ia-orchestrator/library/books").exists()


@pytest.mark.parametrize("change", ["claim", "state", "knowledge", "missing_evidence"])
def test_bundled_evidence_cannot_be_promoted_or_detached_from_knowledge(installed, sourced_run, change):
    base = installed / "ia-orchestrator"
    if change == "claim":
        path = sourced_run / "decisions.yaml"
        doc = yamlio.load(path)
        doc["decisions"][0]["sources"][0]["claim"] += " changed"
        path.write_text(yamlio.dumps(doc), encoding="utf-8")
    elif change == "state":
        path = base / provenance.RUNTIME_EVIDENCE
        doc = yamlio.load(path)
        doc["claims"][0]["support_status"] = "not_supported"
        path.write_text(yamlio.dumps(doc), encoding="utf-8")
    elif change == "knowledge":
        doc = yamlio.load(base / provenance.RUNTIME_EVIDENCE)
        path = base / doc["knowledge_files"][0]["path"]
        path.write_text(path.read_text(encoding="utf-8") + " changed", encoding="utf-8")
    else:
        (base / provenance.RUNTIME_EVIDENCE).unlink()
    code, out = run(installed, "contracts", "check", sourced_run)
    assert code == 1 and out["result"] == "fail", out


def test_bundle_preserves_unverified_and_not_supported_claim_states(installed):
    prov = yamlio.load(ROOT / "library/provenance.yaml")
    receipts = yamlio.load(ROOT / "verification/receipts.yaml")
    from iakit.paths import EXTRACTED
    expected = provenance.derive(prov, receipts, ROOT, EXTRACTED)["states"]
    bundled = yamlio.load(installed / "ia-orchestrator" / provenance.RUNTIME_EVIDENCE)
    for c in bundled["claims"]:
        assert {k: c[k] for k in ("source_verification", "support_status")} == expected[(c["evidence_id"], c["claim_id"])]
