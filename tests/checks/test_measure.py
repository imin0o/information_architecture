"""構造の測定（P2-1、P2-5）と検証計画の実行者・実行状況（P2-4）。"""

import shutil

import pytest

from iakit import contracts, measure, run_cmd, validation_plan, yamlio

from conftest import BUNDLE


@pytest.fixture
def run_dir(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "run")
    return tmp_path / "run"


def page(root, rel, name, links, nav=()):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<a href="{h}">{t}</a>' for h, t in links)
    navs = "".join(f'<a href="{h}">{t}</a>' for h, t in nav)
    path.write_text(f"<html><head><title>{name} | 例</title></head><body><nav>{navs}</nav><h1>{name}</h1>{body}"
                    "</body></html>", encoding="utf-8")


def site(root, south_depth):
    """南地区まで south_depth クリックかかるサイト。孤立ページと文言の揺れを含む。"""
    page(root, "index.html", "トップ", [("area/", "地区から探す")], nav=[("/contact.html", "お問い合わせ")])
    if south_depth == 2:
        page(root, "area/index.html", "地区から探す", [("south.html", "南地区"), ("north.html", "北の地区")])
    else:
        page(root, "area/index.html", "地区から探す", [("more.html", "もっと見る")])
        page(root, "area/more.html", "その他の地区", [("south.html", "南地区"), ("north.html", "北の地区")])
    page(root, "area/south.html", "南地区", [("../index.html", "トップ")])
    page(root, "area/north.html", "北地区", [])
    page(root, "contact.html", "お問い合わせ", [])
    page(root, "old.html", "旧ページ", [("index.html", "トップ")])


def test_sitemap_metrics(run_dir):
    out = measure.structure(run_dir)
    doc = yamlio.load(run_dir / "measurements/structure.yaml")
    assert doc["target"] == {"artifact_id": "ART-004", "version": 1}
    assert [t["clicks"] for t in doc["sitemap"]["tasks"]] == [2, 3, 2, 1]
    match = doc["sitemap"]["label_match"]
    assert (match["NODE-003"], match["NODE-006"], match["NODE-010"]) == ("pref_label", "contains", "mismatch")
    # 語彙と異なるナビのラベルは表記の問題（minor）の候補にとどめる
    assert out["finding_candidates"] == 2
    assert doc["sitemap"]["duplicate_labels"] == {}


def test_threshold_produces_finding_candidates(run_dir):
    measure.structure(run_dir, max_clicks=2)
    cands = yamlio.load(run_dir / "measurements/structure.yaml")["finding_candidates"]
    assert [(c["locator"], c["severity"], c["return_to"]) for c in cands] == [
        ("TASK-02", "major", "structure"), ("NODE-010", "minor", "structure"), ("NODE-011", "minor", "structure")]
    # 候補は iakit finding add の入力にそのまま使える（コマンドが決める項目を含まない）
    assert not set(cands[0]) & {"finding_id", "round", "status", "resolved_in"}


def test_site_crawl_and_baseline_comparison(run_dir, tmp_path):
    site(tmp_path / "after", 2)
    site(tmp_path / "before", 3)
    out = measure.structure(run_dir, tmp_path / "after", tmp_path / "before", max_clicks=2)
    doc = yamlio.load(run_dir / "measurements/structure.yaml")
    m = doc["site"]["metrics"]
    assert m["clicks"]["area/south.html"] == 2 and m["orphans"] == ["old.html"]
    assert m["nav_listed"] == ["contact.html"] and m["one_click"] == ["area/index.html", "contact.html"]
    assert {"text": "北の地区", "dest": "area/north.html", "name": "北地区"} in m["landing_name_mismatch"]
    south = next(t for t in out["comparison"]["tasks"] if t["task_id"] == "TASK-01")
    assert south == {"task_id": "TASK-01", "before": 3, "after": 2}
    locators = {c["locator"] for c in doc["finding_candidates"]}
    assert "サイト: old.html" in locators and "サイト: area/north.html" in locators


def test_baseline_requires_site(run_dir, tmp_path):
    with pytest.raises(ValueError, match="--site"):
        measure.structure(run_dir, baseline=tmp_path)


def plan(rows):
    head = ["## 未検証の判断と検証方法", "", "| 判断 | 方法 | 実行者 | 実行状況 |", "|---|---|---|---|"]
    return head + [f"| IA-001 r2 | {m} | {w} | {s} |" for m, w, s in rows] + ["", "## 方法の適用可否"]


@pytest.mark.parametrize("rows, text", [
    ([("模擬ツリーテスト", "エージェント", "計画")], "実行済みと結果の記録"),
    ([("模擬ツリーテスト", "エージェント", "実行済み")], "実行済みと結果の記録"),
    ([("ツリーテスト", "人", "実行済み")], "STUDY-nnn"),
    ([("ツリーテスト", "外部", "計画")], "実行者「外部」"),
])
def test_validation_plan_rows_are_checked(rows, text):
    assert any(text in e for e in validation_plan.errors(plan(rows), "ART-011"))


def test_validation_plan_accepts_executed_agent_rows_and_planned_human_rows():
    rows = [("模擬ツリーテスト", "エージェント", "実行済み（SIM-001）"),
            ("構造の測定", "エージェント", "実行済み（measurements/structure.yaml）"),
            ("ツリーテスト", "人", "計画")]
    assert validation_plan.errors(plan(rows), "ART-011") == []
    old = ["## 未検証の判断と検証方法", "", "| 判断 | 方法 |", "|---|---|", "| IA-001 r2 | ツリーテスト |"]
    assert any("「実行者」「実行状況」の列がない" in e for e in validation_plan.errors(old, "ART-011"))


@pytest.mark.parametrize("status", ["実行済み（SIM-999）", "実行済み（measurements/missing.yaml）",
                                     "実行済み（measurements/../../outside.yaml）"])
def test_missing_evidence_blocks_registration_and_completion(run_dir, status):
    path = run_dir / "validation-plan.md"
    path.write_text(path.read_text(encoding="utf-8").replace("実行済み（measurements/structure.yaml）", status), encoding="utf-8")
    before = (run_dir / "run.yaml").read_bytes()
    with pytest.raises(ValueError, match="証跡が無効"):
        run_cmd.artifact(run_dir, "validation-plan.md", "validation_plan", [], [])
    assert (run_dir / "run.yaml").read_bytes() == before
    assert any("証跡が無効" in e for e in contracts.check_bundle(run_dir)["errors"])


@pytest.mark.parametrize("change", ["version", "hash", "run_id", "results"])
def test_measurement_must_match_run_and_current_target(run_dir, change):
    path = run_dir / "measurements/structure.yaml"
    doc = yamlio.load(path)
    if change == "version":
        doc["target"]["version"] += 1
    elif change == "hash":
        doc["source_sha256"] = "0" * 64
    elif change == "run_id":
        doc["run_id"] = "RUN-OTHER"
    else:
        doc["sitemap"]["tasks"][0]["clicks"] = 0
    path.write_text(yamlio.dumps(doc), encoding="utf-8")
    assert any("証跡が無効" in e for e in contracts.check_bundle(run_dir)["errors"])
    measure.structure(run_dir)
    assert contracts.check_bundle(run_dir)["errors"] == []


def test_real_simulation_evidence_passes_but_stale_and_invalid_records_fail(run_dir):
    from iakit import simulation_cmd
    from test_simulations import tree_test
    simulation_cmd.add(run_dir, tree_test())
    run = yamlio.load(run_dir / "run.yaml")
    lines = plan([("模擬ツリーテスト", "エージェント", "実行済み（SIM-001）")])
    assert validation_plan.evidence_errors(lines, "plan", run_dir, run) == []
    history = plan([("模擬ツリーテスト", "エージェント", "実行済み（SIM-001）。旧記録 SIM-999 は今回の証跡ではない")])
    assert validation_plan.evidence_errors(history, "plan", run_dir, run) == []
    path = run_dir / "simulations.yaml"
    doc = yamlio.load(path)
    doc["simulations"][0]["results"][0]["success_rate"] = 1.0
    path.write_text(yamlio.dumps(doc), encoding="utf-8")
    assert validation_plan.evidence_errors(lines, "plan", run_dir, run)
    measure.structure(run_dir)
    lines = plan([("構造の測定", "エージェント", "実行済み（measurements/structure.yaml）")])
    next(a for a in run["artifacts"] if a["kind"] == "sitemap")["version"] += 1
    assert validation_plan.evidence_errors(lines, "plan", run_dir, run)


def test_unregistered_source_changes_cannot_be_measured_or_used_as_evidence(run_dir):
    path = run_dir / "sitemap.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# unregistered edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="先に登録し直す"):
        measure.structure(run_dir)
    with pytest.raises(ValueError, match="対象の内容が登録時から変わっている"):
        run_cmd.artifact(run_dir, "validation-plan.md", "validation_plan", [], [])


def test_human_evidence_must_be_an_existing_current_study(run_dir):
    run = yamlio.load(run_dir / "run.yaml")
    lines = plan([("ツリーテスト", "人", "実行済み（STUDY-001）")])
    assert validation_plan.evidence_errors(lines, "plan", run_dir, run) == []
    for status in ("実行済み（STUDY-999）", "実行済み（結果なし）。旧記録 STUDY-001"):
        assert validation_plan.evidence_errors(plan([("ツリーテスト", "人", status)]), "plan", run_dir, run)
