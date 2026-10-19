"""解析データのエクスポートからの調査記録（P4-1）と、tested_* の根拠としての照合。"""

import shutil

import pytest

from iakit import analytics_cmd, contracts, provenance, yamlio

from conftest import BUNDLE

GA4_DEVICES = """# ----------------------------------------
# 端末カテゴリ
# 2026-09-01 - 2026-09-30
# ----------------------------------------
端末カテゴリ,セッション
,"1,000"
mobile,650
desktop,300
tablet,50

# 2つ目の表は読まない
日付,セッション
20260901,30
"""

TRANSITIONS = """前のページ,ページ,表示回数
/,/services/,120
/,/areas/,60
/,/news/,20
/services/,/areas/,80
"""


def spec(**changes):
    data = {"source": "ga4", "aspect": "device", "description": "GA4の端末カテゴリ別セッション（9月）",
            "period_start": "2026-09-01", "period_end": "2026-09-30", "target": "ART-004",
            "dimension": "端末カテゴリ", "metric": "セッション", "value": "mobile", "filters": {},
            "op": "ge", "threshold": 0.6}
    return {**data, **changes}


@pytest.fixture
def run_dir(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "run")
    (tmp_path / "devices.csv").write_text(GA4_DEVICES, encoding="utf-8-sig")
    (tmp_path / "transitions.csv").write_text(TRANSITIONS, encoding="utf-8")
    return tmp_path / "run"


def studies(run_dir):
    return yamlio.load(run_dir / "observations.yaml")["studies"]


def test_import_derives_share_and_result(run_dir):
    out = analytics_cmd.import_export(run_dir, run_dir.parent / "devices.csv", spec())
    # 合計行（次元が空）と2つ目の表を除き、mobile は 650 / 1000
    assert out["result"] == "supported" and out["observed"] == {"matched": 650.0, "total": 1000.0, "share": 0.65}
    study = studies(run_dir)[-1]
    assert study["study_id"] == out["study_id"] and study["method"] == "analytics"
    assert study["target"] == {"artifact_id": "ART-004", "version": 1}
    assert study["analytics"]["period"] == {"start": "2026-09-01", "end": "2026-09-30"}
    inputs = {i["input_id"]: i for i in yamlio.load(run_dir / "inputs.yaml")["inputs"]}
    assert inputs[out["input_id"]]["kind"] == "research_record"
    assert contracts.check_bundle(run_dir)["errors"] == []
    # 同じエクスポートでの2件目は入力を登録し直さない
    again = analytics_cmd.import_export(run_dir, run_dir.parent / "devices.csv", spec(value="desktop", op="le",
                                                                                      threshold=0.2))
    assert again["input_id"] == out["input_id"] and again["result"] == "refuted"


def test_page_transition_filters_population(run_dir):
    out = analytics_cmd.import_export(run_dir, run_dir.parent / "transitions.csv", spec(
        source="other", aspect="page_transition", dimension="ページ", metric="表示回数", value="/services/",
        filters={"前のページ": "/"}, threshold=0.5))
    assert out["observed"]["share"] == 0.6 and out["result"] == "supported"
    small = analytics_cmd.import_export(run_dir, run_dir.parent / "transitions.csv", spec(
        aspect="page_transition", dimension="ページ", metric="表示回数", value="/areas/",
        filters={"前のページ": "/services/"}, threshold=0.5))
    assert small["result"] == "inconclusive"  # 母数 80 < 既定の下限 100


def test_tested_status_uses_analytics_study(run_dir):
    sid = analytics_cmd.import_export(run_dir, run_dir.parent / "devices.csv", spec())["study_id"]
    docs, errors = contracts.load_bundle(run_dir)
    assert errors == []
    d = next(d for d in docs["decisions.yaml"]["decisions"] if d["decision_id"] == "IA-003")
    d.update(validation_status="tested_supported", validation_record_ids=[sid], confidence="high")
    errors = contracts.consistency_errors(run_dir, docs, provenance.registered_sources())[0]
    # 判断を使う成果物は、調査記録の入力を依存に加えて登録し直す
    assert errors == ["ART-001 v1: 判断・評価対象の依存 INPUT-004 が depends_on にない"]
    d.update(validation_status="tested_refuted", confidence="low")
    assert any("調査結果からの導出は tested_supported" in e
               for e in contracts.consistency_errors(run_dir, docs, provenance.registered_sources())[0])


def test_self_reported_values_are_rejected(run_dir):
    analytics_cmd.import_export(run_dir, run_dir.parent / "devices.csv", spec())
    doc = yamlio.load(run_dir / "observations.yaml")
    doc["studies"][-1]["result"] = "refuted"
    doc["studies"][-1]["analytics"]["observed"]["share"] = 0.5
    (run_dir / "observations.yaml").write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    errors = contracts.check_bundle(run_dir)["errors"]
    assert any("自己申告は不可" in e for e in errors) and any("再計算" in e for e in errors)


def test_schema_requires_analytics_block_for_analytics_method(run_dir):
    doc = yamlio.load(run_dir / "observations.yaml")
    doc["studies"][0]["method"] = "analytics"
    assert yamlio.schema_errors(doc, "observation.schema.json")


@pytest.mark.parametrize("changes, message", [
    ({"metric": "ユーザー"}, "列"),
    ({"period_end": "2099-01-01"}, "未来"),
    ({"period_start": "2026-10-01"}, "開始が終了より後"),
    ({"target": "ART-099"}, "現行の成果物ではない"),
    ({"filters": {"端末カテゴリ": "tv"}}, "合計が0"),
])
def test_invalid_import_writes_nothing(run_dir, changes, message):
    before = (run_dir / "inputs.yaml").read_bytes()
    with pytest.raises(ValueError, match=message):
        analytics_cmd.import_export(run_dir, run_dir.parent / "devices.csv", spec(**changes))
    assert (run_dir / "inputs.yaml").read_bytes() == before
    assert all(s["method"] != "analytics" for s in studies(run_dir))
