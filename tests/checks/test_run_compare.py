"""2つの実行の判断比較（NFR-02、AC-08）。"""

import shutil

from iakit import run_compare, yamlio
from iakit.paths import ROOT

BUNDLE = ROOT / "tests" / "fixtures" / "contracts" / "webia-run"


def test_same_run_has_no_decision_difference():
    out = run_compare.compare(BUNDLE, BUNDLE)
    assert out["decisions"] == {"only_in_base": [], "only_in_other": [], "changed": {}}
    assert out["environment"] == "same"


def test_compare_reports_assumption_source_and_status_changes(tmp_path):
    other = tmp_path / "other"
    shutil.copytree(BUNDLE, other)
    doc = yamlio.load(other / "decisions.yaml")
    for d in doc["decisions"]:
        if (d["decision_id"], d["decision_revision"]) == ("IA-001", 2):
            d["assumptions"] = []
        if d["decision_id"] == "IA-003":
            d["status"] = "withdrawn"
    doc["decisions"] = [d for d in doc["decisions"] if d["decision_id"] != "IA-002"]
    (other / "decisions.yaml").write_text(yamlio.dumps(doc), encoding="utf-8")
    out = run_compare.compare(BUNDLE, other)
    assert out["decisions"]["only_in_base"] == ["IA-002"]
    assert set(out["decisions"]["changed"]) == {"IA-001", "IA-003"}
    assert out["decisions"]["changed"]["IA-001"]["fields"]["assumptions"]["other"] == []
    assert out["decisions"]["changed"]["IA-003"]["fields"]["status"] == {"base": "active", "other": "withdrawn"}
