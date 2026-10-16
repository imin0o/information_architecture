import shutil
import subprocess

import pytest

from iakit.paths import FIXTURES_INDEX, MANIFEST, ROOT


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
