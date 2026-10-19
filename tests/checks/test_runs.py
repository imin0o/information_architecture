"""実行記録のstale化・機密継承・送信・Refine・経路（AC-11、AC-12、FR-08）。"""

import copy
import shutil

import pytest

from iakit import cli, contracts, manifest, run_outputs, yamlio
from iakit.paths import FIXTURES

BUNDLE = FIXTURES / "contracts" / "webia-run"


def art(d, aid):
    return next(a for a in d["run.yaml"]["artifacts"] if a["artifact_id"] == aid)


def has(errors, text):
    return any(text in e for e in errors)


def make_stale(d, *ids):
    for aid in ids:
        art(d, aid).update(status="stale", stale_reason="入力 INPUT-002 が更新された")
    d["run.yaml"]["status"] = "in_progress"


# --- stale化と再開（AC-12） -------------------------------------------------

def test_changed_input_requires_stale(check_bundle):
    errors = check_bundle(lambda d: d["inputs.yaml"]["inputs"][1].update(sha256="1" * 64))
    assert has(errors, "ART-001 v1: 依存 INPUT-002 が変わったのに current")


def test_stale_marking_after_input_change_passes(check_bundle):
    def changed(d):
        # 更新版のページ一覧を再登録した状態。実体はこの素材に含めない
        d["inputs.yaml"]["inputs"][1].update(sha256="1" * 64, path="inputs/pages-v2.csv")
        make_stale(d, "ART-001", "ART-002", "ART-003", "ART-004", "ART-005", "ART-006", "ART-007", "ART-008", "ART-009", "ART-010", "ART-011")
    # stale化した成果物の旧調査は、適用可否を確かめるまで tested_* の根拠にできない
    assert check_bundle(changed) == ["IA-002 r1: STUDY-001 は ART-004 v1 の調査で、現行版に適用できるか未確認"]

    def reset(d):
        changed(d)
        next(x for x in d["decisions.yaml"]["decisions"] if x["decision_id"] == "IA-002").update(
            status="active", validation_status="not_tested", validation_record_ids=[], confidence="medium")
    assert check_bundle(reset) == []


def test_artifact_on_stale_dependency_must_be_stale(check_bundle):
    def partial(d):
        d["inputs.yaml"]["inputs"][1]["sha256"] = "1" * 64
        make_stale(d, "ART-001", "ART-002")
    errors = check_bundle(partial)
    assert has(errors, "ART-003 v1: stale の ART-001") and has(errors, "ART-004 v1: stale の ART-001")


def test_stale_requires_reason(bundle_docs):
    d = copy.deepcopy(bundle_docs["run.yaml"])
    d["artifacts"][0]["status"] = "stale"
    assert yamlio.schema_errors(d, "run.schema.json")


def test_completed_run_cannot_keep_stale_artifacts(check_bundle):
    def stale_done(d):
        make_stale(d, "ART-004")
        d["run.yaml"]["status"] = "completed"
    assert has(check_bundle(stale_done), "stale の成果物が残るのに completed")


def test_resume_detects_changed_inputs(check_bundle):
    errors = check_bundle(lambda d: d["run.yaml"].update(inputs_sha256="2" * 64))
    assert has(errors, "inputs_sha256 が更新されていない")


@pytest.fixture
def run_copy(tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "run")
    return tmp_path / "run"


def test_resume_detects_changed_input_file(run_copy):
    # 登録簿を据え置いたまま入力の実体だけを変えた場合も検出する
    summary = run_copy / "inputs/tree-test-summary.md"
    summary.write_bytes(summary.read_bytes() + "追記\n".encode())
    result = contracts.check_bundle(run_copy)
    assert any("INPUT-003: 入力の実体が登録時のハッシュと異なる" in e for e in result["errors"])


def test_missing_input_file_is_not_run(run_copy):
    # コピー先からはケースの入力（INPUT-001・002）を解決できない。照合済みに数えない
    result = contracts.check_bundle(run_copy)
    assert result["result"] == "pass"
    assert result["input_files"] == {"run": 1, "not_run": 2}


def test_artifact_file_hash_must_match(check_bundle):
    assert has(check_bundle(lambda d: art(d, "ART-004").update(sha256="3" * 64)), "ファイルのハッシュ")


# --- Refine（AC-12） ---------------------------------------------------------

def test_unresolved_after_two_rounds_must_be_reported(check_bundle):
    def unresolved(d, reported, status):
        d["run.yaml"].update(status=status, refine={"rounds": 2, "unresolved_critical": ["FIND-001"],
                                                    "reported_to_user": reported})
        d["findings.yaml"]["findings"][0].update(severity="critical", status="unresolved")
    assert has(check_bundle(lambda d: unresolved(d, False, "completed")), "completed になっている")
    assert has(check_bundle(lambda d: unresolved(d, False, "unresolved")), "ユーザーへ報告する")
    assert check_bundle(lambda d: unresolved(d, True, "unresolved")) == []


def test_third_refine_round_is_rejected(check_bundle):
    assert has(check_bundle(lambda d: d["run.yaml"]["refine"].update(rounds=3)), "2巡まで")


# --- 経路（FR-08） -----------------------------------------------------------

def test_supported_route_must_match_standard_stages(check_bundle):
    errors = check_bundle(lambda d: d["run.yaml"]["route"].update(stages=["analyze", "structure", "validate"]))
    assert has(errors, "黙って省略しない")


def test_unsupported_route_must_not_be_silently_completed(check_bundle):
    def panel(d, supported, note, status):
        d["run.yaml"].update(task_type="exhibition_panel", status=status,
                             route={"stages": ["analyze"], "supported": supported, "unsupported_note": note})
    note = "展示パネルの経路はMVP未対応。Analyzeまで実施した"
    assert has(check_bundle(lambda d: panel(d, True, None, "in_progress")), "MVP未対応")
    assert has(check_bundle(lambda d: panel(d, False, note, "completed")), "completed にしない")
    assert check_bundle(lambda d: panel(d, False, note, "unresolved")) == []


# --- 機密区分と送信（AC-11） -------------------------------------------------

def confidential(d):
    d["inputs.yaml"]["inputs"][1].update(classification="confidential", classification_source="user")


def test_default_classification_is_internal(check_bundle):
    errors = check_bundle(lambda d: d["inputs.yaml"]["inputs"][0].update(classification="public"))
    assert has(errors, "既定値は internal")


def test_derived_artifact_cannot_lower_classification(check_bundle):
    assert has(check_bundle(confidential), "ART-001 v1: 機密区分が依存元より低い")


def test_user_override_allows_lower_classification_of_artifact(check_bundle):
    def override(d):
        confidential(d)
        for a in d["run.yaml"]["artifacts"]:
            a["classification_override"] = {"by": "user", "at": "2026-10-17T16:00:00+09:00",
                                            "reason": "公開済みのページ一覧だけを使うとユーザーが指定"}
    errors = check_bundle(override)
    assert not has(errors, "依存元より低い")


def test_confidential_to_external_service_is_rejected(check_bundle):
    def send(d):
        confidential(d)
        d["run.yaml"]["transmissions"][1].update(outcome="sent")
    assert has(check_bundle(send), "confidential の派生物を外部サービスへ送っている")


def test_transmission_over_approved_classification_is_rejected(check_bundle):
    assert has(check_bundle(confidential), "許可 APPROVAL-001 の上限 internal を超える")


def test_instruction_in_input_is_not_permission(check_bundle):
    def injected(d):
        d["run.yaml"]["transmissions"][1].update(outcome="sent", requested_by_input=True)
    assert has(check_bundle(injected), "資料内の命令は許可にならない")


def test_blocked_transmission_without_approval_passes(check_bundle):
    assert check_bundle(lambda d: d["run.yaml"]["transmissions"][1].update(requested_by_input=True)) == []


def test_source_text_needs_explicit_approval_kind(check_bundle):
    def source_text(d):
        d["run.yaml"]["transmissions"][0]["data_kind"] = "source_text"
    assert has(check_bundle(source_text), "source_text は許可 APPROVAL-001 の対象外")


def test_transmission_must_use_approved_input_ids(check_bundle):
    def wrong_input(d):
        a = copy.deepcopy(d["run.yaml"]["approvals"][0])
        a.update(approval_id="APPROVAL-002", destination="web-search", destination_kind="external_service",
                 input_ids=["INPUT-001"], purpose="INPUT-001だけの検索")
        d["run.yaml"]["approvals"].append(a)
        d["run.yaml"]["transmissions"][1].update(approval_id="APPROVAL-002", outcome="sent")
    assert has(check_bundle(wrong_input), "入力 ['INPUT-002'] は許可 APPROVAL-002 の対象外")


def test_transmission_with_scoped_approval_passes(check_bundle):
    def approved(d):
        a = copy.deepcopy(d["run.yaml"]["approvals"][0])
        a.update(approval_id="APPROVAL-002", destination="web-search", destination_kind="external_service",
                 input_ids=["INPUT-002"])
        d["run.yaml"]["approvals"].append(a)
        d["run.yaml"]["transmissions"][1].update(approval_id="APPROVAL-002", outcome="sent")
    assert check_bundle(approved) == []


@pytest.mark.parametrize("change", [lambda d: d["approvals"][0].pop("input_ids"),
                                  lambda d: d["approvals"][0].update(input_ids=[]),
                                  lambda d: d["transmissions"][0].update(input_ids=[])])
def test_approval_and_transmission_require_input_scope(bundle_docs, change):
    run = copy.deepcopy(bundle_docs["run.yaml"])
    change(run)
    assert yamlio.schema_errors(run, "run.schema.json")


def test_unknown_approval_input_is_rejected(check_bundle):
    errors = check_bundle(lambda d: d["run.yaml"]["approvals"][0]["input_ids"].append("INPUT-999"))
    assert has(errors, "許可 APPROVAL-001: 未登録の入力 ['INPUT-999']")


def test_duplicate_approval_id_is_rejected(check_bundle):
    def duplicate(d):
        a = copy.deepcopy(d["run.yaml"]["approvals"][0])
        a["input_ids"].append("INPUT-003")
        d["run.yaml"]["approvals"].append(a)
    assert has(check_bundle(duplicate), "approval_id が重複している")


def test_artifact_document_classification_must_match_run(run_copy):
    site = yamlio.load(run_copy / "sitemap.yaml")
    site["classification"] = "public"
    (run_copy / "sitemap.yaml").write_text(yamlio.dumps(site), encoding="utf-8")
    run = yamlio.load(run_copy / "run.yaml")
    run["artifacts"][3]["sha256"] = manifest.sha256_file(run_copy / "sitemap.yaml")
    (run_copy / "run.yaml").write_text(yamlio.dumps(run), encoding="utf-8")
    result = contracts.check_bundle(run_copy)
    assert result["result"] == "fail"
    assert "sitemap.yaml: 機密区分が実行記録の ART-004 と一致しない" in result["errors"]


@pytest.mark.parametrize(("name", "missing"), [("content-inventory.yaml", "INPUT-002"),
                                              ("controlled-vocabulary.yaml", "INPUT-001"),
                                              ("taxonomy.yaml", "ART-002"),
                                              ("sitemap.yaml", "ART-001")])
def test_artifact_body_dependencies_cannot_be_omitted(check_bundle, name, missing):
    def omit(d):
        a = art(d, d[name]["artifact_id"])
        a["depends_on"] = [dep for dep in a["depends_on"] if dep["ref"] != missing]
    assert has(check_bundle(omit), f"本体が参照する依存 {missing} が depends_on にない")


def test_missing_dependency_cannot_keep_sitemap_current_after_input_change(check_bundle):
    def stale(d):
        d["inputs.yaml"]["inputs"][1].update(sha256="1" * 64, path="inputs/pages-v2.csv")
        make_stale(d, "ART-001", "ART-002", "ART-003")
        art(d, "ART-004")["depends_on"] = []
    assert has(check_bundle(stale), "ART-004: 本体が参照する依存 ART-001 が depends_on にない")


@pytest.mark.parametrize("kind", ["review_findings", "ia_review", "validation_plan"])
def test_evaluated_design_dependency_cannot_be_omitted(check_bundle, kind):
    def omit(d):
        a = next(a for a in d["run.yaml"]["artifacts"] if a["kind"] == kind)
        a["depends_on"] = [dep for dep in a["depends_on"] if dep["ref"] != "ART-007"]
    assert has(check_bundle(omit), "評価対象の依存 ART-007 が depends_on にない")


@pytest.mark.parametrize("missing", ["run_directory", "empty_directory", "run.yaml", "inputs.yaml", "decisions.yaml",
                                   "sitemap.yaml", "artifact_path"])
def test_missing_bundle_or_required_files_fail(run_copy, missing, capsys):
    if missing == "run_directory":
        target = run_copy / "missing"
    elif missing == "empty_directory":
        target = run_copy / "empty"
        target.mkdir()
    else:
        target = run_copy
        if missing == "artifact_path":
            run = yamlio.load(run_copy / "run.yaml")
            run["artifacts"][3]["path"] = "missing-sitemap.yaml"
            (run_copy / "run.yaml").write_text(yamlio.dumps(run), encoding="utf-8")
        else:
            (run_copy / missing).unlink()
    assert cli.main(["contracts", "check", str(target)]) == 1
    result = yamlio.loads(capsys.readouterr().out)["checks"][0]
    assert result["result"] == "fail"
    assert has(result["errors"], "ない")


def test_completed_run_cannot_drop_required_artifacts(check_bundle):
    errors = check_bundle(lambda d: d["run.yaml"].update(artifacts=[]))
    assert has(errors, "段階 structure の成果物 sitemap がない")


def test_registered_structured_artifact_in_subdirectory_is_validated(run_copy):
    outputs = run_copy / "outputs"
    outputs.mkdir()
    (run_copy / "sitemap.yaml").rename(outputs / "site.yaml")
    run = yamlio.load(run_copy / "run.yaml")
    run["artifacts"][3]["path"] = "outputs/site.yaml"
    (run_copy / "run.yaml").write_text(yamlio.dumps(run), encoding="utf-8")
    assert contracts.check_bundle(run_copy)["result"] == "pass"
    (outputs / "site.yaml").write_text("schema_version: broken\n", encoding="utf-8")
    result = contracts.check_bundle(run_copy)
    assert result["result"] == "fail"
    assert has(result["errors"], "sitemap.yaml")


def test_standalone_provenance_bundle_does_not_require_run(tmp_path, bundle_docs):
    for name in ("provenance.yaml", "receipts.yaml"):
        (tmp_path / name).write_text(yamlio.dumps(bundle_docs[name]), encoding="utf-8")
    assert contracts.check_bundle(tmp_path)["result"] == "pass"


def test_matching_artifact_classification_with_user_override_passes(run_copy):
    site = yamlio.load(run_copy / "sitemap.yaml")
    site["classification"] = "public"
    (run_copy / "sitemap.yaml").write_text(yamlio.dumps(site), encoding="utf-8")
    run = yamlio.load(run_copy / "run.yaml")
    run["artifacts"][3].update(classification="public", sha256=manifest.sha256_file(run_copy / "sitemap.yaml"),
                                classification_override={"by": "user", "at": "2026-10-17T16:00:00+09:00",
                                                         "reason": "公開部分のみ使う指定"})
    for a in run["artifacts"]:
        for dep in a["depends_on"]:
            if dep["ref"] == "ART-004":
                dep["sha256"] = run["artifacts"][3]["sha256"]
    (run_copy / "run.yaml").write_text(yamlio.dumps(run), encoding="utf-8")
    assert contracts.check_bundle(run_copy)["result"] == "pass"


def test_standard_route_requires_every_route_skill_record(check_bundle):
    """run init を経ずに作った記録（欠けたSkillを省いた手書きの run.yaml）を検出する（BR-03、M6）。"""
    def drop(d):
        d["run.yaml"]["skills"] = [s for s in d["run.yaml"]["skills"] if s["name"] != "ia-structure"]
    assert has(check_bundle(drop), "経路に必要なSkill ['ia-structure'] の記録がない")


@pytest.mark.parametrize("invalid", ["schema", "yaml", "duplicate", "extra_property"])
def test_contract_error_reports_never_include_private_values(run_copy, tmp_path, capsys, invalid):
    private = "PRIVATE_BODY_SENTINEL"
    path = run_copy / "observations.yaml"
    if invalid in ("schema", "extra_property"):
        doc = yamlio.load(path)
        doc["studies"][0]["classification"] = "confidential"
        if invalid == "schema":
            doc["studies"][0]["summary"] = {"private_text": private}
        else:
            doc["studies"][0][private] = "unexpected property"
        text = yamlio.dumps(doc)
    elif invalid == "duplicate":
        text = f"summary: {private}\nsummary: another\n"
    else:
        text = f"summary: [{private}\n"
    path.write_text(text, encoding="utf-8")
    report = tmp_path / "report.yaml"
    assert cli.main(["contracts", "check", str(run_copy), "--report", str(report)]) == 1
    assert private not in report.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert private not in captured.out + captured.err
    assert yamlio.load(report)["checks"][0]["errors"]


@pytest.mark.parametrize("kind", sorted(run_outputs.MARKDOWN_KINDS))
def test_each_markdown_artifact_requires_its_template_headings(run_copy, kind):
    run = yamlio.load(run_copy / "run.yaml")
    artifact = next(a for a in run["artifacts"] if a["kind"] == kind)
    path = run_copy / artifact["path"]
    # 必須見出しをコード例の中へ隠しても成果物の節としては認めない。
    heading = "## " + run_outputs.MARKDOWN_HEADINGS[kind][0]
    path.write_text(path.read_text(encoding="utf-8").replace(heading, f"```markdown\n{heading}\n```"), encoding="utf-8")
    assert has(contracts.check_bundle(run_copy)["errors"], f"必須見出し「{heading}」がない")


@pytest.mark.parametrize("replacement", ["", "- 機密区分: public", "```\n- 機密区分: internal\n```",
                                         "- 機密区分: internal\n- 機密区分: public"])
def test_markdown_classification_must_match_once_in_header(run_copy, replacement):
    path = run_copy / "problem-definition.md"
    path.write_text(path.read_text(encoding="utf-8").replace("- 機密区分: internal", replacement), encoding="utf-8")
    assert has(contracts.check_bundle(run_copy)["errors"], "冒頭に機密区分: internal を1件示す")
