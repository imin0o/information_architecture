import hashlib
import io
import zipfile

import pytest

from iakit import fixture_epub, fixture_pdf, leaks, yamlio
from conftest import git


@pytest.mark.parametrize("filename,payload", [
    ("input.json", '{"confidentiality":"confidential"}'),
    ("input.json", '{"items":[{"classification":"confidential"}]}'),
    ("input.yaml", '"confidentiality": confidential\n'),
    ("input.yaml", "- confidentiality: confidential\n"),
    ("input.yaml", "items: [{classification: confidential}]\n"),
    ("input.md", "---\nclassification: confidential\n---\nSelf-written body\n"),
    ("input.md", "--- heading\nclassification: confidential\n"),
    ("input.md", "---\nclassification: public\n---\nclassification: confidential\n"),
])
def test_structured_confidentiality(filename, payload):
    reasons = leaks.inspect(filename, payload.encode(), set(), {})
    assert "confidential 区分のデータ" in reasons


@pytest.mark.parametrize("filename,payload", [
    ("input.json", '{"confidentiality":'),
    ("input.json", '{"value": NaN}'),
    ("input.yaml", "a: 1\na: 2\n"),
    ("input.md", "---\nclassification: public\n"),
])
def test_unreadable_metadata_blocks_inspection(filename, payload):
    assert leaks.inspect(filename, payload.encode(), set(), {})


@pytest.mark.parametrize("builder", [fixture_pdf.build, fixture_epub.build])
def test_book_magic_detects_renamed_unregistered_books(builder):
    assert leaks.inspect("notes/renamed.bin", builder(), set(), {})


def test_regular_zip_is_not_a_book():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("note.txt", "Self-written")
    assert leaks.inspect("archive.zip", stream.getvalue(), set(), {}) == []


def test_worktree_manifest_cannot_remove_indexed_hash(repo):
    content = b"synthetic original payload"
    path = repo / "sources/manifest.yaml"
    data = yamlio.load(path)
    data["books"][0]["original"].update(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
    path.write_text(yamlio.dumps(data), encoding="utf-8")
    (repo / "renamed.bin").write_bytes(content)
    git(repo, "add", ".")
    data["books"][0]["original"]["sha256"] = None
    path.write_text(yamlio.dumps(data), encoding="utf-8")
    assert any(v["reason"] == "登録済み原典と同一内容" for v in leaks.check(root=repo)["violations"])


def test_worktree_fixture_allowance_cannot_change_verdict(repo):
    path = repo / "tests/fixtures/pdf/page-labels.pdf"
    payload = b"%PDF-1.7 synthetic unapproved fixture"
    path.write_bytes(payload)
    git(repo, "add", "tests/fixtures/pdf/page-labels.pdf")
    index_path = repo / "tests/fixtures/fixtures.yaml"
    index = yamlio.load(index_path)
    index["files"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
    index_path.write_text(yamlio.dumps(index), encoding="utf-8")
    result = leaks.check(root=repo)
    assert result["result"] == "fail" and result["violations"]
    git(repo, "add", "tests/fixtures/fixtures.yaml")
    result = leaks.check(root=repo)
    assert result["result"] == "fail" and result["errors"]


@pytest.mark.parametrize("path", ["sources/manifest.yaml", "tests/fixtures/fixtures.yaml"])
def test_missing_policy_is_failure(repo, path):
    git(repo, "rm", "--cached", path)
    assert leaks.check(root=repo)["errors"]


def test_broken_staged_policy_is_failure(repo):
    (repo / "sources/manifest.yaml").write_text("books: []\n", encoding="utf-8")
    git(repo, "add", "sources/manifest.yaml")
    assert leaks.check(root=repo)["errors"]


def test_index_bytes_are_checked_instead_of_worktree(repo):
    path = repo / "note.json"
    path.write_text('{"classification":"confidential"}', encoding="utf-8")
    git(repo, "add", "note.json")
    path.write_text('{"classification":"public"}', encoding="utf-8")
    assert leaks.check(root=repo)["violations"]
