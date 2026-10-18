"""代表ケースの配置先照合と、失敗したRefineの再試行。ホスト呼び出しだけを置き換える。"""

import shutil
from pathlib import Path

import pytest

from iakit import accept, accept_hostenv, accept_route, manifest, run_cmd, yamlio
from iakit.paths import ROOT

BUNDLE = ROOT / "tests/fixtures/contracts/webia-run"
RUN_ID = "RUN-FIXTURE-001"


def snapshot(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


@pytest.fixture
def route_run(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    case = root / "tests/cases/webia-001/case.yaml"
    case.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests/cases/webia-001/case.yaml", case)
    template = tmp_path / "template"
    shutil.copytree(BUNDLE, template)
    inputs = yamlio.load(template / "inputs.yaml")
    for item in inputs["inputs"]:
        source = BUNDLE / item["path"]
        target = template / "inputs" / source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        item["path"] = target.relative_to(template).as_posix()
    run_cmd.save(template, "inputs.yaml", inputs)
    run = yamlio.load(template / "run.yaml")
    run["inputs_sha256"] = manifest.sha256_file(template / "inputs.yaml")
    run["skills"] = [run_cmd.skill_item(s["name"]) for s in run["skills"]]
    run["environment"]["host"] = "codex"
    run_cmd.save(template, "run.yaml", run)
    monkeypatch.setattr(accept_route, "ROOT", root)
    # 名前・内容ハッシュの照合には実際のSkillを使い、ホストのモデルだけを置き換える。
    def workspace(base, host, names, case):
        cwd = base / "workspace"
        cwd.mkdir()
        return cwd, ROOT / "skills"
    monkeypatch.setattr(accept_hostenv, "_workspace", workspace)
    monkeypatch.setattr(accept, "DIST", tmp_path / "no-distributions")

    def invoke(mutate=None, code=0, refine=False, produce=True):
        def host_call(host, cwd, prompt, timeout):
            produced = cwd / "runs" / RUN_ID
            if produce and not produced.exists():
                shutil.copytree(template, produced)
            if mutate:
                mutate(produced)
            return code, "new host response"
        monkeypatch.setattr(accept_hostenv, "_host_call", host_call)
        return accept_route.route("codex", "webia-001", RUN_ID, refine=refine)

    return invoke, root / "runs" / RUN_ID


def test_route_accepts_matching_skills_and_case_inputs_with_extra_input(route_run):
    invoke, dest = route_run
    result = invoke()
    assert result["result"] == "pass", result
    assert result["contracts"] == "pass"
    assert result["started_with_bundled_runtime"]["skills_match_placement"]
    # 自作素材には固定ケースの2入力に加えて調査記録がある。追加を欠落と取り違えない。
    assert len(yamlio.load(dest / "inputs.yaml")["inputs"]) == 3
    assert result["started_with_bundled_runtime"]["inputs_registered"]


@pytest.mark.parametrize("mismatch", ["skill", "task", "host", "input"])
def test_route_rejects_records_that_do_not_match_placement_or_case(route_run, mismatch):
    invoke, _ = route_run
    def mutate(produced):
        run = yamlio.load(produced / "run.yaml")
        if mismatch == "skill":
            run["skills"][0]["sha256"] = "0" * 64
        elif mismatch == "task":
            run["task_type"] = "taxonomy"
        elif mismatch == "host":
            run["environment"]["host"] = "claude-code"
        else:
            inputs = yamlio.load(produced / "inputs.yaml")
            inputs["inputs"][0]["sha256"] = "0" * 64
            run_cmd.save(produced, "inputs.yaml", inputs)
        run_cmd.save(produced, "run.yaml", run)
    result = invoke(mutate)
    assert result["result"] == "fail"
    expected = {"skill": "skills_match_placement", "task": "task_type", "host": "ホスト", "input": "inputs_registered"}
    assert any(expected[mismatch] in e for e in result["errors"])


def test_route_without_run_record_fails(route_run):
    invoke, dest = route_run
    result = invoke(produce=False)
    assert result["result"] == "fail" and not dest.exists()
    assert not result["started_with_bundled_runtime"]["run_yaml"]


@pytest.mark.parametrize("failure", ["missing_host", "timeout", "placement", "contracts"])
def test_failed_refine_preserves_original_run_and_can_be_retried(route_run, failure):
    invoke, dest = route_run
    assert invoke()["result"] == "pass"
    (dest / "rubric-judgment.yaml").write_text("verdict: fail\n", encoding="utf-8")
    before = snapshot(dest)
    def mutate(produced):
        (produced / "attempt.txt").write_text("partial work", encoding="utf-8")
        if failure == "placement":
            run = yamlio.load(produced / "run.yaml")
            run["skills"][0]["sha256"] = "0" * 64
            run_cmd.save(produced, "run.yaml", run)
        elif failure == "contracts":
            (produced / "content-inventory.yaml").write_text("invalid: true\n", encoding="utf-8")
    code = {"missing_host": 127, "timeout": 124}.get(failure, 0)
    result = invoke(mutate, code=code, refine=True)
    assert result["result"] == "fail"
    assert snapshot(dest) == before
    # 元の判定を手で復元せず、同じコマンドで再試行できる。
    retried = invoke(refine=True)
    assert retried["result"] == "pass", retried
    rounds = yamlio.load(dest / "run.yaml")["refine"]["rounds"]
    assert (dest / f"rubric-judgment.round{rounds}.yaml").read_bytes() == before["rubric-judgment.yaml"]
    assert (dest / f"host-final-message.round{rounds}.txt").read_bytes() == before["host-final-message.txt"]
    assert not (dest / "rubric-judgment.yaml").exists()
    assert not (dest / "attempt.txt").exists()


@pytest.mark.parametrize("failure", ["copy", "install"])
def test_failed_refine_collection_keeps_original_and_cleans_staging(tmp_path, monkeypatch, failure):
    dest, produced = tmp_path / "run", tmp_path / "produced"
    for path in (dest, produced):
        path.mkdir()
        (path / "rubric-judgment.yaml").write_text("verdict: fail\n", encoding="utf-8")
    (dest / "old.txt").write_text("original run", encoding="utf-8")
    before = snapshot(dest)
    copytree, rename = shutil.copytree, Path.rename
    def fail_copy(src, dst, *args, **kwargs):
        if Path(dst).name == "candidate":
            raise OSError("copy failed")
        return copytree(src, dst, *args, **kwargs)
    def fail_install(path, target):
        if path.name == "candidate":
            raise OSError("install failed")
        return rename(path, target)
    if failure == "copy":
        monkeypatch.setattr(shutil, "copytree", fail_copy)
    else:
        monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(OSError, match="failed"):
        accept_route._collect(produced, dest, "new response", 1)
    assert snapshot(dest) == before
    assert not list(tmp_path.glob(".run-refine-*"))
