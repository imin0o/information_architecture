"""配布物の生成・再生成・混在検出（BR-03、AC-02）。チェックアウトの写しで生成し、実リポジトリの dist/ は使わない。"""

import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from iakit import dist, distverify, manifest, provenance, yamlio
from iakit.paths import ROOT

ENTRY = ("import sys; sys.stdout.reconfigure(encoding='utf-8'); sys.path.insert(0, sys.argv[1]); "
         "from iakit.cli import main; sys.exit(main(sys.argv[2:]))")


def iakit(root, *args):
    done = subprocess.run([sys.executable, "-I", "-c", ENTRY, str(root / "scripts"), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", cwd=root)
    assert done.stdout, done.stderr
    return done.returncode, yamlio.loads(done.stdout)["checks"][0]


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    """Gitで追跡・追跡予定のファイルだけを写したチェックアウト（原典・固定抽出・dist/ を含まない）。"""
    root = tmp_path_factory.mktemp("checkout") / "repo copy"
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                            capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    for rel in filter(None, listed):
        if (ROOT / rel).is_file() and not rel.startswith("dist/"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, root / rel)
    code, out = iakit(root, "dist", "build")
    assert code == 0, out
    shutil.copytree(root / "dist", root.parent / "base")
    return root


@pytest.fixture
def repo(checkout, tmp_path):
    """試験ごとに変更できる写し。基準の配布物を dist/ に置いた状態から始める。"""
    root = tmp_path / "repo"
    shutil.copytree(checkout, root)
    return root


def changed(out):
    return sorted(n for n, d in out["diff"].items() if d["status"] == "changed")


def rereview_test_file(root, rel):
    """試験用checkoutで再レビュー完了を模擬する。実ライブラリの照合記録は変更しない。"""
    path = root / "verification/receipts.yaml"
    doc = yamlio.load(path)
    for rc in doc["receipts"]:
        for item in rc["targets"]["knowledge_files"]:
            if item["path"] == rel:
                item["sha256"] = manifest.sha256_file(root / rel)
    path.write_text(yamlio.dumps(doc), encoding="utf-8")


def test_regeneration_from_same_input_is_identical(repo):
    code, out = iakit(repo, "dist", "build")
    assert code == 0 and changed(out) == []
    assert iakit(repo, "dist", "check")[1]["result"] == "pass"


def test_checkout_without_originals_matches_repository_record(checkout):
    """原典・固定抽出のない環境（CI）でも、同じ配布IDになる。"""
    recorded = yamlio.load(ROOT / "verification/distributions.yaml")["distributions"]
    assert yamlio.load(checkout / "verification/distributions.yaml")["distributions"] == recorded


def test_skill_specific_change_updates_only_that_skill(repo):
    path = repo / "skills/ia-analysis/templates/information-audit.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n補足\n", encoding="utf-8")
    code, out = iakit(repo, "dist", "build")
    assert code == 0 and changed(out) == ["ia-analysis"]
    # 照合記録は同梱ファイルのハッシュを持つため、同じ配布物の中で一緒に変わる
    assert out["diff"]["ia-analysis"]["changed"] == ["templates/information-audit.md", "verification/bundle-receipt.yaml"]


def test_shared_knowledge_change_updates_every_dependent_skill(repo, checkout):
    users = sorted(n for n in ("ia-analysis", "ia-evaluation", "ia-structure", "ia-taxonomy")
                   if "KN-005" in (repo / "skills" / n / "SKILL.md").read_text(encoding="utf-8"))
    path = repo / "library/concepts/label-consistency.md"
    path.write_text(path.read_text(encoding="utf-8").replace("## 仮説・補完", "## 仮説・補完\n\n追記。\n", 1),
                    encoding="utf-8")
    code, out = iakit(repo, "dist", "build")
    assert code == 1 and "照合・レビュー記録がない" in out["errors"][0]
    assert iakit(repo, "dist", "check")[0] == 1
    assert all(d["status"] == "unchanged" for d in dist.diff(repo / "dist", checkout.parent / "base").values())
    rereview_test_file(repo, "library/concepts/label-consistency.md")
    code, out = iakit(repo, "dist", "build")
    assert code == 0, out
    assert changed(out) == sorted(users + ["ia-orchestrator"])
    assert all("library/concepts/label-consistency.md" in out["diff"][n]["changed"] for n in users)


def test_unrelated_book_note_does_not_rewrite_distributions(repo):
    note = repo / "library/books/BOOK-002/notes.md"
    note.parent.mkdir(parents=True)
    note.write_text("# BOOK-002 文献ノート\n\n未統合。\n", encoding="utf-8")
    index = yamlio.load(repo / "library/index.yaml")
    index["entries"].append({"knowledge_id": "KN-099", "kind": "book_note", "title": "BOOK-002 文献ノート",
                             "path": "library/books/BOOK-002/notes.md", "claims": [], "terms": [], "conflicts": []})
    (repo / "library/index.yaml").write_text(yamlio.dumps(index), encoding="utf-8")
    code, out = iakit(repo, "dist", "build")
    assert code == 0 and changed(out) == [], out


def test_added_knowledge_updates_only_skills_that_reference_it(repo):
    """文献追加で新しい知識を足し、参照するSkill（と全知識を持つ ia-orchestrator）だけが変わる（AC-08の更新処理）。"""
    body = repo / "library/methods/added-method.md"
    # 新しい根拠・主張と、その本文に結び付く確認済み記録を試験用checkoutにだけ作る。
    prov = yamlio.load(repo / "library/provenance.yaml")
    record = deepcopy(prov["records"][0])
    record["evidence_id"] = "EV-9999"
    claim = deepcopy(record["support_reviews"][0])
    claim["claim_id"] = "CLAIM-9999"
    record["support_reviews"] = [claim]
    prov["records"].append(record)
    (repo / "library/provenance.yaml").write_text(yamlio.dumps(prov), encoding="utf-8")
    body.write_text(f"# 追加した方法\n\n{claim['claim']} [CLAIM-9999]。\n", encoding="utf-8")
    receipts = yamlio.load(repo / "verification/receipts.yaml")
    rc = deepcopy(receipts["receipts"][0])
    rc.update(receipt_id="VR-9999", evidence_id="EV-9999")
    rc["targets"].update(record_sha256=provenance.record_sha256(record),
                         knowledge_files=[{"path": "library/methods/added-method.md", "sha256": manifest.sha256_file(body)}])
    rc["claims"] = [{"claim_id": "CLAIM-9999", "claim_sha256": claim["claim_sha256"], "status": claim["status"],
                     "applicability_sha256": provenance.text_sha256(claim["applicability"])}]
    receipts["receipts"].append(rc)
    (repo / "verification/receipts.yaml").write_text(yamlio.dumps(receipts), encoding="utf-8")
    index = yamlio.load(repo / "library/index.yaml")
    index["entries"].append({"knowledge_id": "KN-099", "kind": "method", "title": "追加した方法",
                             "path": "library/methods/added-method.md", "claims": ["CLAIM-9999"],
                             "terms": [], "conflicts": []})
    (repo / "library/index.yaml").write_text(yamlio.dumps(index), encoding="utf-8")
    skill = repo / "skills/ia-taxonomy/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8").replace(
        "| KN-002 同名異義語の区別 |", "| KN-099 追加した方法 | 手順2。 |\n| KN-002 同名異義語の区別 |", 1), encoding="utf-8")
    code, out = iakit(repo, "dist", "build")
    assert code == 0, out
    assert changed(out) == ["ia-orchestrator", "ia-taxonomy"]
    assert "library/methods/added-method.md" in out["diff"]["ia-taxonomy"]["added"]


def test_deleted_reference_does_not_remain(repo):
    (repo / "skills/ia-structure/templates/information-architecture.md").unlink()
    text = (repo / "skills/ia-structure/SKILL.md").read_text(encoding="utf-8")
    (repo / "skills/ia-structure/SKILL.md").write_text(
        text.replace("(templates/information-architecture.md)", "（削除）"), encoding="utf-8")
    code, out = iakit(repo, "dist", "build")
    assert code == 0 and changed(out) == ["ia-structure"], out
    assert out["diff"]["ia-structure"]["removed"] == ["templates/information-architecture.md"]
    assert not (repo / "dist/ia-structure/templates/information-architecture.md").exists()


def test_broken_reference_stops_build_and_keeps_previous_dist(repo):
    (repo / "skills/ia-structure/templates/information-architecture.md").unlink()
    code, out = iakit(repo, "dist", "build")
    assert code == 1 and "解決できない参照" in out["errors"][0]
    assert (repo / "dist/ia-structure/templates/information-architecture.md").is_file()
    assert not list(repo.glob(".dist-new-*"))


def test_stale_record_fails_check(repo):
    path = repo / "skills/ia-taxonomy/SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    code, out = iakit(repo, "dist", "check")
    assert code == 1 and "distributions.yaml" in out["errors"][-1]


def test_mixed_distributions_are_detected(repo, checkout):
    path = repo / "library/concepts/label-consistency.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    rereview_test_file(repo, "library/concepts/label-consistency.md")
    assert iakit(repo, "dist", "build")[0] == 0
    mixed = repo / "mixed"
    shutil.copytree(repo / "dist", mixed)
    shutil.rmtree(mixed / "ia-taxonomy")
    shutil.copytree(checkout.parent / "base" / "ia-taxonomy", mixed / "ia-taxonomy")
    errors = distverify.set_errors(mixed, sorted(p.name for p in mixed.iterdir()))
    assert any(e.startswith("KN-005:") for e in errors), errors


@pytest.mark.parametrize("change", ["edit", "extra", "remove_dependency", "manifest", "missing_skill", "nested_manifest"])
def test_installed_distribution_check(checkout, tmp_path, change):
    installed = tmp_path / "installed skills"
    shutil.copytree(checkout.parent / "base", installed)
    if change == "edit":
        (installed / "ia-taxonomy/SKILL.md").write_text("changed", encoding="utf-8")
    elif change == "extra":
        (installed / "ia-taxonomy/templates/old.md").write_text("old", encoding="utf-8")
    elif change == "remove_dependency":
        shutil.rmtree(installed / "ia-structure")
    elif change == "missing_skill":
        (installed / "ia-orchestrator/SKILL.md").unlink()
    elif change == "nested_manifest":
        (installed / "ia-taxonomy/templates/build-manifest.yaml").write_text("extra", encoding="utf-8")
    else:
        doc = yamlio.load(installed / "ia-taxonomy" / distverify.MANIFEST_NAME)
        doc["files"] = doc["files"][1:]
        (installed / "ia-taxonomy" / distverify.MANIFEST_NAME).write_text(yamlio.dumps(doc), encoding="utf-8")
    errors = distverify.set_errors(installed, sorted(p.name for p in installed.iterdir()))
    assert errors, change
    if change == "remove_dependency":
        assert errors == ["ia-orchestrator: 依存Skill ia-structure が配置されていない"]
    if change in ("missing_skill", "nested_manifest"):
        assert dist.check(installed)["result"] == "fail"


def test_bundled_terms_and_conflicts_have_their_claims(checkout):
    for skill in (checkout / "dist").iterdir():
        claims = {c["claim_id"] for c in yamlio.load(skill / provenance.RUNTIME_EVIDENCE)["claims"]}
        terms = yamlio.load(skill / "library/terms.yaml")["terms"]
        conflicts = yamlio.load(skill / "library/conflicts.yaml")["conflicts"]
        assert {c for t in terms for c in t["claims"]} <= claims
        assert {p["claim_id"] for c in conflicts for p in c["positions"]} <= claims


def test_installed_partial_review_is_rejected_even_with_matching_manifest(checkout, tmp_path):
    skill = tmp_path / "ia-taxonomy"
    shutil.copytree(checkout.parent / "base/ia-taxonomy", skill)
    path = skill / provenance.RUNTIME_EVIDENCE
    evidence = yamlio.load(path)
    evidence["claims"][0]["source_verification"] = "unverified"
    path.write_text(yamlio.dumps(evidence), encoding="utf-8")
    doc = yamlio.load(skill / distverify.MANIFEST_NAME)
    for item in doc["files"]:
        if item["path"] == provenance.RUNTIME_EVIDENCE:
            item["sha256"] = manifest.sha256_file(path)
    doc["distribution_id"] = distverify.distribution_id(doc)
    (skill / distverify.MANIFEST_NAME).write_text(yamlio.dumps(doc), encoding="utf-8")
    errors = distverify.set_errors(tmp_path, ["ia-taxonomy"])
    assert any("原典照合・主張レビューがない" in e for e in errors), errors


@pytest.mark.parametrize("failure", [None, "install", "restore", "record"])
def test_replace_preserves_unrelated_backup_and_restores_previous_dist(tmp_path, monkeypatch, failure):
    out = tmp_path / "release"
    out.mkdir()
    (out / "previous.txt").write_text("previous", encoding="utf-8")
    unrelated = tmp_path / ".release-old"
    unrelated.mkdir()
    (unrelated / "unrelated.txt").write_text("keep", encoding="utf-8")
    record = tmp_path / "distributions.yaml"
    record.write_text("previous record", encoding="utf-8")

    def generate(work):
        (work / "new.txt").write_text("new", encoding="utf-8")
        return []

    rename = Path.rename

    def checked_rename(path, target):
        if failure in ("install", "restore") and path.name.startswith(".release-new-"):
            raise OSError("install failed")
        if failure == "restore" and path.name == "previous":
            raise OSError("restore failed")
        return rename(path, target)

    monkeypatch.setattr(dist, "_generate", generate)
    monkeypatch.setattr(dist, "verify", lambda root: [])
    monkeypatch.setattr(dist, "diff", lambda *args: {})
    monkeypatch.setattr(Path, "rename", checked_rename)
    replace = Path.replace

    def checked_replace(path, target):
        if failure == "record" and target == record:
            raise OSError("record failed")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", checked_replace)
    if failure:
        with pytest.raises(OSError, match="復元に失敗した" if failure == "restore" else f"{failure} failed"):
            dist.build(out, record=record)
        if failure == "restore":
            assert len(list(tmp_path.glob(".release-old-*/previous/previous.txt"))) == 1
        else:
            assert (out / "previous.txt").read_text(encoding="utf-8") == "previous"
            assert not list(tmp_path.glob(".release-old-*"))
    else:
        assert dist.build(out, record=record)["result"] == "pass"
        assert (out / "new.txt").is_file() and not (out / "previous.txt").exists()
        assert not list(tmp_path.glob(".release-old-*"))
    assert (unrelated / "unrelated.txt").read_text(encoding="utf-8") == "keep"
    if failure:
        assert record.read_text(encoding="utf-8") == "previous record"
    assert not list(tmp_path.glob(".distributions.yaml-*"))
    assert not list(tmp_path.glob(".release-new-*"))


def test_real_pre_push_checks_committed_tip_instead_of_worktree(repo, tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout.decode().strip()

    git("init", "-q")
    git("config", "user.name", "iakit test")
    git("config", "user.email", "test@example.invalid")
    git("config", "core.autocrlf", "false")
    git("add", "--all")
    git("-c", "core.hooksPath=.git/no-hooks", "commit", "-qm", "valid candidate")
    valid = git("rev-parse", "HEAD")
    skill = repo / "skills/ia-taxonomy/SKILL.md"
    original = skill.read_bytes()
    skill.write_bytes(original + b"\nUnrecorded skill update\n")
    git("add", str(skill))
    git("-c", "core.hooksPath=.git/no-hooks", "commit", "-qm", "stale distribution record")
    skill.write_bytes(original)  # 作業ツリーの修正で古い配布記録を隠せないこと。
    git("config", "core.hooksPath", ".githooks")
    remote = tmp_path / "local-only.git"
    git("init", "--bare", str(remote))
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    blocked = subprocess.run(["git", "push", str(remote), "HEAD:refs/heads/bad"], cwd=repo, env=env, capture_output=True)
    assert blocked.returncode != 0
    assert b"distributions.yaml" in blocked.stdout + blocked.stderr
    assert not (remote / "refs/heads/bad").exists()
    # HEAD以外の正常なコミットは、作業ツリーに不整合があっても対象コミットの検査で通る。
    skill.write_bytes(original + b"\nDirty worktree\n")
    allowed = subprocess.run(["git", "push", str(remote), f"{valid}:refs/heads/good"], cwd=repo, env=env, capture_output=True)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert (remote / "refs/heads/good").read_text().strip() == valid


def run_installed(installed, *args):
    entry = installed / "ia-orchestrator" / "scripts" / "run_iakit.py"
    done = subprocess.run([sys.executable, "-I", str(entry), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", cwd=installed.parent)
    return done.returncode, yamlio.loads(done.stdout) if done.stdout else {"stderr": done.stderr}


def test_installed_distribution_runs_and_refuses_mixed_content(checkout, tmp_path):
    installed = tmp_path / "moved skills"
    shutil.copytree(checkout.parent / "base", installed)
    args = ("--task-type", "taxonomy", "--host", "codex", "--model", "m")
    code, out = run_installed(installed, "run", "init", tmp_path / "RUN-D-001", "--run-id", "RUN-D-001", *args)
    assert code == 0, out
    (installed / "ia-taxonomy/SKILL.md").write_text(
        (installed / "ia-taxonomy/SKILL.md").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    code, out = run_installed(installed, "run", "init", tmp_path / "RUN-D-002", "--run-id", "RUN-D-002", *args)
    assert code == 1 and "配布物の検査に失敗" in out["error"], out
    # 経路に要らない ia-structure の欠落では止めない
    shutil.rmtree(installed / "ia-taxonomy")
    shutil.copytree(checkout.parent / "base" / "ia-taxonomy", installed / "ia-taxonomy")
    shutil.rmtree(installed / "ia-structure")
    code, out = run_installed(installed, "run", "init", tmp_path / "RUN-D-003", "--run-id", "RUN-D-003", *args)
    assert code == 0, out


def test_accept_reseal_simulates_other_library_and_is_detected(checkout, tmp_path):
    from iakit import accept
    installed = tmp_path / "skills"
    shutil.copytree(checkout.parent / "base", installed)
    accept._reseal(installed / "ia-taxonomy")
    assert not distverify.skill_errors(installed / "ia-taxonomy")[1]
    errors = distverify.set_errors(installed, ["ia-orchestrator", "ia-taxonomy"])
    assert any("内容が異なる" in e for e in errors), errors


@pytest.mark.parametrize("expect,code,text,ok", [
    ("pass", 0, "", True), ("pass", 1, "error: x", False),
    ("stop", 1, "error: 必要なSkillがない", True), ("stop", 1, "error: 別の理由", False), ("stop", 0, "", False)])
def test_accept_record_requires_expected_stop_reason(expect, code, text, ok):
    from iakit import accept
    checks = []
    accept._record(checks, "c", expect, code, text, "必要なSkillがない")
    assert (checks[0]["result"] == "pass") is ok


def test_accept_host_started_requires_hashes_from_placement(checkout, tmp_path):
    """ホストの実行が同梱の実行時処理で始まったことを、Skillの内容ハッシュと配置先の一致で判定する。"""
    from iakit import accept_hostenv as accept_host
    workspace = tmp_path / "work space"
    skills = workspace / ".agents" / "skills"
    shutil.copytree(checkout.parent / "base", skills)
    code, out = run_installed(skills, "run", "init", workspace / "runs" / "RUN-H-001", "--run-id", "RUN-H-001",
                              "--task-type", "web_ia", "--host", "codex", "--model", "m")
    assert code == 0, out
    obs = accept_host._started(workspace, skills, "RUN-H-001", "web_ia", 0)
    assert obs["skills_match_placement"] and obs["route_skills"] == 5 and obs["inputs_registered"], obs
    path = workspace / "runs" / "RUN-H-001" / "run.yaml"
    doc = yamlio.load(path)
    doc["skills"][0]["sha256"] = "0" * 64
    path.write_text(yamlio.dumps(doc), encoding="utf-8")
    assert not accept_host._started(workspace, skills, "RUN-H-001", "web_ia", 0)["skills_match_placement"]
    assert accept_host._started(workspace, skills, "RUN-H-404", "web_ia", 0) == {"run_yaml": False}


def test_accept_isolated_env_hides_build_source(monkeypatch):
    """配置先のホストにビルド元の仮想環境とパスを渡さない（M6でビルド元の skills/ を使う混入を検出した）。"""
    from iakit import accept
    from iakit.paths import ROOT
    monkeypatch.setenv("VIRTUAL_ENV", str(ROOT / ".venv"))
    monkeypatch.setenv("PATH", str(ROOT / ".venv" / "Scripts") + os.pathsep + "C:\tools")
    monkeypatch.setenv("PWD", str(ROOT))
    env = accept.isolated_env()
    assert "VIRTUAL_ENV" not in env and "PWD" not in env and env["PATH"] == "C:\tools"
