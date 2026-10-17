"""課題ケースの固定状態、CLI、配布manifestの契約（10章、AC-06、AC-09、BR-03）。"""

import re
import shutil

import pytest

from iakit import cases, cli, yamlio
from iakit.paths import FIXTURES, ROOT


@pytest.fixture
def case_root(tmp_path):
    root = tmp_path / "cases"
    shutil.copytree(cases.CASES, root)
    return root


def edit(path, fn):
    data = yamlio.load(path)
    fn(data)
    path.write_text(yamlio.dumps(data), encoding="utf-8", newline="\n")


def test_repository_cases_are_fixed_and_complete():
    result = cases.check()
    assert result["errors"] == []
    assert set(result["cases"]) == {"CASE-WEBIA-001", "CASE-TAXONOMY-001", "CASE-BOUNDARY-001"}
    assert result["content_review"] == "human_only"


def test_changed_case_input_is_detected(case_root):
    path = case_root / "webia-001/inputs/pages.csv"
    path.write_bytes(path.read_bytes() + b"P-15,new,,,,info\n")
    assert any("固定した版と異なる" in e for e in cases.check(case_root)["errors"])


def test_missing_rubric_item_is_detected(case_root):
    edit(case_root / "taxonomy-001/case.yaml", lambda c: c["rubric"].pop())
    assert any("ルーブリック全項目" in e for e in cases.check(case_root)["errors"])


def test_rubric_version_mismatch_is_detected(case_root):
    edit(case_root / "rubric.yaml", lambda r: r.update(rubric_version="1.2.0", changes=[
        {"version": "1.2.0", "reason": "試験"}]))
    assert any("rubric_version" in e for e in cases.check(case_root)["errors"])


def test_version_bump_requires_change_reason(case_root):
    edit(case_root / "webia-001/case.yaml", lambda c: c.update(case_version="1.2.0"))
    assert any("変更理由" in e for e in cases.check(case_root)["errors"])


def test_missing_machine_check_is_detected(case_root):
    edit(case_root / "webia-001/case.yaml",
         lambda c: c["scenarios"][0]["machine_checks"].append("tests/checks/test_runs.py::test_does_not_exist"))
    assert any("test_does_not_exist" in e for e in cases.check(case_root)["errors"])


def test_missing_scenario_kind_is_detected(case_root):
    shutil.rmtree(case_root / "boundary-001")
    errors = cases.check(case_root)["errors"]
    assert any("unsupported_route" in e for e in errors)


def test_missing_representative_case_is_detected(case_root):
    shutil.rmtree(case_root / "taxonomy-001")
    assert any("代表ケースが不足" in e for e in cases.check(case_root)["errors"])


def test_not_applicable_item_needs_reason(case_root):
    edit(case_root / "taxonomy-001/case.yaml", lambda c: c["rubric"][4].update(not_applicable_reason=None))
    assert cases.check(case_root)["result"] == "fail"


def test_cli_contracts_and_cases_pass(capsys):
    assert cli.main(["contracts", "check", str(FIXTURES / "contracts/webia-run")]) == 0
    out = yamlio.loads(capsys.readouterr().out)
    assert out["checks"][0]["human_source_check"] == "not_run"
    assert out["checks"][0]["existing_receipts"] == {"valid": 1, "stale": []}
    assert cli.main(["cases", "check"]) == 0


def test_cli_contracts_fails_on_broken_bundle(tmp_path, capsys):
    shutil.copytree(FIXTURES / "contracts/webia-run", tmp_path / "run")
    edit(tmp_path / "run/run.yaml", lambda r: r.update(status="done"))
    assert cli.main(["contracts", "check", str(tmp_path / "run")]) == 1
    assert "run.yaml status" in capsys.readouterr().out


def _requirements_example(marker: str) -> str:
    text = (ROOT / "requirements.md").read_text(encoding="utf-8")
    after = text[text.index(marker):]
    start = after.index("```yaml\n") + len("```yaml\n")
    # 形式例のハッシュは「…」で省略している。形式検査のために64桁の値へ置き換える
    return re.sub(r'(\w*sha256: )"…"', r'\1"' + "0" * 64 + '"', after[start:after.index("```", start)])


@pytest.mark.parametrize(("marker", "schema"), [
    ("次は`decisions.yaml`の形式例", "decision.schema.json"),
    ("`library/provenance.yaml`は次の形式とする", "provenance.schema.json"),
])
def test_requirements_examples_follow_schema(marker, schema):
    # 要件書の記録例とスキーマが再び乖離しないようにする
    assert yamlio.schema_errors(yamlio.loads(_requirements_example(marker)), schema) == []


BUILD = {
    "schema_version": "1.0", "skill": {"name": "ia-taxonomy", "version": "0.1.0"}, "contract_version": "1.0.0",
    "requires_skills": [{"name": "ia-analysis", "contract_version": "1.0.0"}],
    "knowledge": [{"id": "CONCEPT-001", "sha256": "a" * 64}], "schemas": [{"path": "schemas/taxonomy.schema.json",
                                                                         "sha256": "b" * 64}],
    "files": [{"path": "SKILL.md", "sha256": "c" * 64}], "verification_receipts": ["VR-0001"],
    "tool": {"name": "iakit", "version": "0.1.0"}, "distribution_id": "DIST-0123456789abcdef",
}


def test_build_manifest_contract_accepts_relative_paths():
    assert yamlio.schema_errors(BUILD, "build-manifest.schema.json") == []


@pytest.mark.parametrize("path", ["../library/methods/x.md", "/abs/SKILL.md", "refs\\x.md"])
def test_build_manifest_rejects_paths_outside_skill(path):
    data = {**BUILD, "files": [{"path": path, "sha256": "c" * 64}]}
    assert yamlio.schema_errors(data, "build-manifest.schema.json")
