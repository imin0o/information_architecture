"""配布物に同梱する実行時処理が、リポジトリなしで動くこと（D-M4-10、P-M4-02）。"""

import shutil
import subprocess
import sys

import pytest

from iakit import runtime, yamlio
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


def test_bundled_runtime_reports_missing_skill(installed, tmp_path):
    shutil.rmtree(installed / "ia-taxonomy")
    code, out = run(installed, "run", "init", tmp_path / "RUN-X-002", "--run-id", "RUN-X-002",
                    "--task-type", "taxonomy", "--host", "codex", "--model", "m")
    assert code == 1 and "ia-taxonomy" in out["error"]
