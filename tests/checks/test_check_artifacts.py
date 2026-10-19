"""分野Skillに同梱する単独利用の形式検査（scripts/check_artifacts.py、P-M6-01）。"""

import importlib.util
import shutil
import subprocess
import sys

import pytest

from iakit import contracts, runtime, yamlio
from iakit.paths import FIXTURES, ROOT

RUN = FIXTURES / "contracts" / "webia-run"
spec = importlib.util.spec_from_file_location("check_artifacts", ROOT / "scripts" / "check_artifacts.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_file_map_follows_contracts():
    assert checker.FILES.items() <= contracts.SCHEMAS.items()
    assert set(contracts.ARTIFACT_FILES.values()) <= set(checker.FILES)


def test_valid_run_passes_and_broken_inventory_fails(tmp_path):
    out = checker.check(RUN)
    assert out["result"] == "pass", out["errors"]
    assert "content-inventory.yaml" in out["checked"] and out["contracts_check"] == "not_run"
    shutil.copyfile(RUN / "content-inventory.yaml", tmp_path / "content-inventory.yaml")
    doc = yamlio.load(tmp_path / "content-inventory.yaml")
    del doc["items"][0]["locator"]
    (tmp_path / "content-inventory.yaml").write_text(yamlio.dumps(doc), encoding="utf-8")
    out = checker.check(tmp_path)
    assert out["result"] == "fail" and any("locator" in e for e in out["errors"])


def test_empty_directory_is_not_a_pass(tmp_path):
    assert checker.check(tmp_path)["result"] == "fail"


@pytest.mark.parametrize("invalid", ["schema", "yaml", "extra_property"])
def test_standalone_check_does_not_echo_private_values(tmp_path, invalid):
    private = "PRIVATE_BODY_SENTINEL"
    doc = yamlio.load(RUN / "content-inventory.yaml")
    if invalid == "extra_property":
        doc[private] = "unexpected property"
    else:
        doc["items"][0]["title"] = {"private_text": private}
    text = f"title: [{private}\n" if invalid == "yaml" else yamlio.dumps(doc)
    (tmp_path / "content-inventory.yaml").write_text(text, encoding="utf-8")
    report = checker.check(tmp_path)
    assert report["result"] == "fail" and private not in yamlio.dumps(report)


def test_bundled_copy_uses_only_its_own_schemas(tmp_path):
    """配布物と同じ配置（scripts/ と schemas/）に写すと、リポジトリなしで動く。"""
    skill = tmp_path / "ia-taxonomy"
    runtime.bundle_schemas(skill, {"controlled-vocabulary.schema.json", "taxonomy.schema.json"})
    runtime.bundle_checker(skill)
    work = tmp_path / "out"
    work.mkdir()
    for name in ("controlled-vocabulary.yaml", "taxonomy.yaml", "sitemap.yaml"):
        shutil.copyfile(RUN / name, work / name)
    done = subprocess.run([sys.executable, "-I", str(skill / "scripts" / "check_artifacts.py"), str(work)],
                          capture_output=True, text=True, encoding="utf-8", cwd=tmp_path)
    out = yamlio.loads(done.stdout)
    assert done.returncode == 0, out
    assert out["checked"] == ["controlled-vocabulary.yaml", "taxonomy.yaml"]
    assert out["not_checked"] == ["sitemap.yaml"]
