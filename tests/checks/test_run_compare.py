"""2つの実行の判断比較（NFR-02、AC-08）。"""

import shutil

import pytest

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


@pytest.mark.parametrize("field", ["assumptions", "alternatives", "sources", "rationale", "counter_evidence",
                                  "validation_record_ids", "stage"])
def test_compare_keeps_subfields_that_explain_adoption(tmp_path, field):
    other = tmp_path / "other"
    shutil.copytree(BUNDLE, other)
    doc = yamlio.load(other / "decisions.yaml")
    decision = next(d for d in doc["decisions"] if d["decision_id"] == "IA-001" and d["decision_revision"] == 2)
    if field == "assumptions":
        decision[field][0]["critical"] = not decision[field][0]["critical"]
    elif field == "alternatives":
        decision[field][0]["rejected_because"] += " changed"
    elif field == "sources":
        decision[field][0]["primary"] = not decision[field][0]["primary"]
    elif field == "counter_evidence":
        decision[field] = [{"ref": "OBS-999", "resolved": False, "note": "new counter evidence"}]
    elif field == "validation_record_ids":
        decision[field] = ["STUDY-999"]
    else:
        decision[field] = "validate" if field == "stage" else decision[field] + " changed"
    (other / "decisions.yaml").write_text(yamlio.dumps(doc), encoding="utf-8")
    fields = run_compare.compare(BUNDLE, other)["decisions"]["changed"]["IA-001"]["fields"]
    assert set(fields) == {field}
    assert fields[field]["base"] != fields[field]["other"]


def test_compare_reports_generation_settings(tmp_path):
    other = tmp_path / "other"
    shutil.copytree(BUNDLE, other)
    doc = yamlio.load(other / "run.yaml")
    doc["environment"]["settings"]["temperature"] = 0.25
    (other / "run.yaml").write_text(yamlio.dumps(doc), encoding="utf-8")
    assert run_compare.compare(BUNDLE, other)["environment"]["other"]["settings"] == {"temperature": 0.25}
