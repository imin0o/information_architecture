import copy
import shutil
import subprocess

import pytest

from iakit import contracts, provenance
from iakit.paths import FIXTURES, FIXTURES_INDEX, MANIFEST, ROOT

BUNDLE = FIXTURES / "contracts" / "webia-run"


@pytest.fixture(scope="session")
def bundle_docs():
    docs, errors = contracts.load_bundle(BUNDLE)
    assert errors == []
    return docs


@pytest.fixture
def check_bundle(bundle_docs):
    """正常な自作素材を変更し、整合性検査のエラーを返す。"""
    def run(mutate=None):
        docs = copy.deepcopy(bundle_docs)
        if mutate:
            mutate(docs)
        return contracts.consistency_errors(BUNDLE, docs, provenance.registered_sources())[0]
    return run


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "iakit test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    for src in (MANIFEST, FIXTURES_INDEX, ROOT / ".gitignore", ROOT / ".gitattributes"):
        dst = tmp_path / src.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    shutil.copytree(FIXTURES_INDEX.parent / "pdf", tmp_path / "tests/fixtures/pdf")
    git(tmp_path, "add", "sources/manifest.yaml", "tests/fixtures", ".gitignore")
    return tmp_path
