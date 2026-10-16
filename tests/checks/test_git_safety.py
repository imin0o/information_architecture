import copy
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from iakit import fixture_pdf, fixtures, leak_history, leaks, yamlio
from iakit.paths import ROOT
from conftest import git


def commit(repo, message):
    git(repo, "-c", "core.hooksPath=.git/no-hooks", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def updates(tip, old=None):
    return f"refs/heads/main {tip} refs/heads/main {old or '0' * 40}\n"


def test_pre_push_catches_added_then_deleted_confidential_file(repo):
    initial = commit(repo, "initial")
    note = repo / "note.json"
    note.write_text('{"confidentiality":"confidential"}', encoding="utf-8")
    git(repo, "add", "note.json")
    unsafe = commit(repo, "add confidential")
    git(repo, "rm", "note.json")
    tip = commit(repo, "remove confidential")
    assert leaks.check(root=repo)["result"] == "pass"
    result = leak_history.check_push(updates(tip, initial), "origin", root=repo)
    assert result["result"] == "fail"
    assert any(v["target"].endswith(unsafe) for check in result["checks"] for v in check["violations"])


def test_new_branch_checks_history_and_deletion_needs_no_scan(repo):
    tip = commit(repo, "initial")
    assert leak_history.check_push(updates(tip), "origin", root=repo)["result"] == "pass"
    assert leak_history.check_push(updates("0" * 40, tip), "origin", root=repo)["commits_scanned"] == 0


def test_legacy_history_without_catalogue_uses_tip_original_hashes(repo):
    import hashlib
    from iakit import yamlio

    payload = b"synthetic original without a book header"
    git(repo, "rm", "--cached", "sources/manifest.yaml", "tests/fixtures/fixtures.yaml")
    (repo / "renamed.bin").write_bytes(payload)
    git(repo, "add", "renamed.bin")
    commit(repo, "before catalogue")
    path = repo / "sources/manifest.yaml"
    data = yamlio.load(path)
    data["books"][0]["original"].update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    path.write_text(yamlio.dumps(data), encoding="utf-8")
    git(repo, "rm", "renamed.bin")
    git(repo, "add", "sources/manifest.yaml", "tests/fixtures/fixtures.yaml")
    tip = commit(repo, "catalogue")
    assert leak_history.check_push(updates(tip), "origin", root=repo)["result"] == "fail"


@pytest.mark.parametrize("value", ["bad input", "refs/heads/main invalid refs/heads/main invalid"])
def test_bad_push_input_fails(repo, value):
    assert leak_history.check_push(value, "origin", root=repo)["result"] == "fail"


def test_push_accepts_approved_old_fixtures_after_generator_update(repo, monkeypatch):
    generator = repo / "scripts/iakit/fixture_pdf.py"
    generator.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/iakit/fixture_pdf.py", generator)
    git(repo, "add", "scripts")
    old = commit(repo, "initial fixtures")
    pages = copy.deepcopy(fixture_pdf.PAGES)
    pages[0][1][0] = "Revised Fixture Handbook"
    generator.write_text(generator.read_text(encoding="utf-8").replace(
        "Fixture Handbook", "Revised Fixture Handbook"), encoding="utf-8")
    monkeypatch.setattr(fixture_pdf, "PAGES", pages)
    files, catalogue = fixtures.render()
    for relative, content in files.items():
        path = repo / "tests/fixtures" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (repo / "tests/fixtures/fixtures.yaml").write_text(catalogue, encoding="utf-8")
    git(repo, "add", "scripts", "tests/fixtures")
    tip = commit(repo, "revise fixtures and generator")
    assert leaks.check(root=repo)["result"] == "pass"
    result = leak_history.check_push(updates(tip), "origin", root=repo)
    assert result["result"] == "pass"
    assert result["commits_scanned"] == 2
    # 別ブランチの旧先端も、現在の生成器に依存せず検査できる。
    assert leak_history.check_push(updates(old), "origin", root=repo)["result"] == "pass"
    # index検査には旧版の例外を適用しない。
    git(repo, "read-tree", old)
    assert leaks.check(root=repo)["result"] == "fail"


@pytest.mark.parametrize("tamper_catalogue", [False, True])
def test_push_rejects_unapproved_historical_fixture(repo, tamper_catalogue):
    initial = commit(repo, "initial fixtures")
    path = repo / "tests/fixtures/pdf/page-labels.pdf"
    payload = b"%PDF-1.7 unapproved self-written fixture"
    path.write_bytes(payload)
    if tamper_catalogue:
        catalogue = repo / "tests/fixtures/fixtures.yaml"
        data = yamlio.load(catalogue)
        data["files"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
        catalogue.write_text(yamlio.dumps(data), encoding="utf-8")
    git(repo, "add", "tests/fixtures")
    unsafe = commit(repo, "unapproved historical fixture")
    git(repo, "restore", "--source=" + initial, "--staged", "--worktree", "tests/fixtures")
    tip = commit(repo, "restore approved fixture")
    result = leak_history.check_push(updates(tip), "origin", root=repo)
    assert result["result"] == "fail"
    check = result["checks"][0]
    findings = check["errors"] if tamper_catalogue else check["violations"]
    assert any(f["target"].endswith(unsafe) for f in findings)


def test_attributes_preserve_fixture_bytes_and_hook_lf_with_autocrlf(repo):
    shutil.copytree(ROOT / ".githooks", repo / ".githooks")
    git(repo, "config", "core.autocrlf", "true")
    git(repo, "add", ".githooks", ".gitattributes")
    checkout = repo / ".tmp/checkout"
    for relative in ("tests/fixtures/pdf/page-labels.pdf", ".githooks/pre-commit"):
        git(repo, "checkout-index", "--prefix=" + checkout.as_posix() + "/", "--", relative)
        assert (checkout / relative).read_bytes() == (repo / relative).read_bytes()


def test_real_hooks_block_commit_a_and_push_after_no_verify(repo):
    # 同じ入口・シェルスクリプトを使い、実原典を含まないcheckoutを作る。
    for filename in ("pyproject.toml", "uv.lock", ".python-version"):
        shutil.copyfile(ROOT / filename, repo / filename)
    shutil.copytree(ROOT / "scripts/iakit", repo / "scripts/iakit", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "schemas", repo / "schemas")
    shutil.copytree(ROOT / ".githooks", repo / ".githooks")
    note = repo / "note.json"
    note.write_text('{"classification":"public"}', encoding="utf-8")
    git(repo, "add", ".")
    initial = commit(repo, "initial")
    git(repo, "config", "core.hooksPath", ".githooks")
    env = dict(os.environ, PYTHONUTF8="1")
    # verify.pyの一時環境を子checkoutに引き継がせない。
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    note.write_text('{"classification":"confidential"}', encoding="utf-8")
    blocked = subprocess.run(["git", "commit", "-am", "blocked"], cwd=repo, env=env, capture_output=True)
    assert blocked.returncode != 0
    assert git(repo, "rev-parse", "HEAD").stdout.decode().strip() == initial
    assert b"confidential" in blocked.stderr  # 起動失敗を停止成功と誤認しない
    subprocess.run(["git", "commit", "--no-verify", "-am", "bypass"], cwd=repo, env=env, check=True,
                   capture_output=True)
    git(repo, "rm", "note.json")
    tip = commit(repo, "delete before push")
    # Git自身にpre-pushを起動させる。失敗時は空のbareリポジトリへ何も送られない。
    remote = repo / ".tmp/remote.git"
    git(repo, "init", "--bare", str(remote))
    pushed = subprocess.run(["git", "push", str(remote), "HEAD:refs/heads/main"], cwd=repo, env=env,
                            capture_output=True)
    assert pushed.returncode != 0
    assert b"confidential" in pushed.stdout + pushed.stderr
    assert git(remote, "show-ref", check=False).returncode == 1
    # --no-verifyで作ったコミットが履歴検査の対象になったことも確認する。
    assert leak_history.check_push(updates(tip, initial), "origin", root=repo)["result"] == "fail"
