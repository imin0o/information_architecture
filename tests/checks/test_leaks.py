import hashlib
import subprocess

import pytest

from iakit import leaks, yamlio
from iakit.paths import ROOT
from conftest import git


def staged(repo, rel, content: bytes, force=False):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    git(repo, "add", *(["-f"] if force else []), rel)
    return leaks.check(root=repo, staged_only=True)


def reasons(result):
    return {v["reason"] for v in result["violations"]}


def test_clean_index_passes(repo):
    assert leaks.check(root=repo)["result"] == "pass"


def test_gitignore_blocks_plain_add_of_originals(repo):
    (repo / "sources/originals").mkdir(parents=True)
    (repo / "sources/originals/book.pdf").write_bytes(b"%PDF-1.7")
    proc = subprocess.run(["git", "-C", str(repo), "add", "sources/originals/book.pdf"], capture_output=True)
    assert proc.returncode != 0


def test_force_added_original_is_detected(repo):
    result = staged(repo, "sources/originals/book.epub", b"PK self-made", force=True)
    assert result["result"] == "fail"
    assert "sources/ は manifest.yaml 以外を管理しない" in reasons(result)


def test_renamed_copy_of_known_original_is_detected(repo):
    content = b"synthetic original with no format header"
    path = repo / "sources/manifest.yaml"
    data = yamlio.load(path)
    data["books"][0]["original"].update(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
    path.write_text(yamlio.dumps(data), encoding="utf-8")
    git(repo, "add", "sources/manifest.yaml")
    result = staged(repo, "notes/reading.bin", content)
    assert "登録済み原典と同一内容" in reasons(result)


def test_unlisted_book_file_in_fixtures_is_detected(repo):
    result = staged(repo, "tests/fixtures/pdf/other.pdf", b"%PDF-1.7 not listed")
    assert result["result"] == "fail"


@pytest.mark.parametrize("kind", ["missing", "empty", "file"])
def test_invalid_directory_fails(tmp_path, kind):
    target = tmp_path / "target"
    if kind == "empty":
        target.mkdir()
    elif kind == "file":
        target.write_bytes(b"not a directory")
    result = leaks.check(root=ROOT, dirs=[target])
    assert result["result"] == "fail" and result["errors"]


@pytest.mark.parametrize("path", [
    "sources/originals/book.pdf", "sources/extracted/EXTRACT-001/text.txt", "sources/new/data.bin",
    "runs/R-001/run.yaml", "library/build-logs/raw/output.txt", "dist/iakit.whl", "build/output.py",
    ".tmp/check.txt", ".venv-test/python.exe", "scripts/iakit/__pycache__/cli.pyc",
])
def test_gitignore_excludes_private_and_generated_paths(repo, path):
    assert git(repo, "check-ignore", "--no-index", path, check=False).returncode == 0


@pytest.mark.parametrize("path", [
    "sources/manifest.yaml", "tests/fixtures/pdf/page-labels.pdf", "tests/fixtures/fixtures.yaml",
    "library/build-logs/checks/m0-review.yaml", "scripts/verify.py", ".github/workflows/checks.yml",
])
def test_gitignore_keeps_sources_and_evidence(repo, path):
    assert git(repo, "check-ignore", "--no-index", path, check=False).returncode == 1


def test_run_records_are_detected(repo):
    result = staged(repo, "runs/R-001/run.yaml", b"run_id: R-001\n", force=True)
    assert "実行記録・生ログはGit管理外" in reasons(result)


def test_confidential_input_is_detected(repo):
    body = b"input_id: IN-001\n" + b"confidentiality: " + b"confidential\n"
    result = staged(repo, "notes/input.yaml", body)
    assert "confidential 区分のデータ" in reasons(result)


def test_dist_directory_check_rejects_book_files(tmp_path):
    dist = tmp_path / "dist" / "ia-analysis"
    dist.mkdir(parents=True)
    (dist / "excerpt.pdf").write_bytes(b"%PDF-1.7")
    result = leaks.check(root=ROOT, dirs=[tmp_path / "dist"])
    assert result["result"] == "fail"
