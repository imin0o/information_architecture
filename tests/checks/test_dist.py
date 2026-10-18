"""配布物の生成・再生成・混在検出（BR-03、AC-02）。チェックアウトの写しで生成し、実リポジトリの dist/ は使わない。"""

import shutil
import subprocess
import sys

import pytest

from iakit import distverify, yamlio
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


def test_shared_knowledge_change_updates_every_dependent_skill(repo):
    users = sorted(n for n in ("ia-analysis", "ia-evaluation", "ia-structure", "ia-taxonomy")
                   if "KN-005" in (repo / "skills" / n / "SKILL.md").read_text(encoding="utf-8"))
    path = repo / "library/concepts/label-consistency.md"
    path.write_text(path.read_text(encoding="utf-8").replace("## 仮説・補完", "## 仮説・補完\n\n追記。\n", 1),
                    encoding="utf-8")
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
    body.write_text("# 追加した方法\n\n同名異義語を区別する [CLAIM-0001]。\n\n## 仮説・補完\n\nなし\n", encoding="utf-8")
    index = yamlio.load(repo / "library/index.yaml")
    index["entries"].append({"knowledge_id": "KN-099", "kind": "method", "title": "追加した方法",
                             "path": "library/methods/added-method.md", "claims": ["CLAIM-0001"],
                             "terms": ["TERM-0001"], "conflicts": []})
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
    assert iakit(repo, "dist", "build")[0] == 0
    mixed = repo / "mixed"
    shutil.copytree(repo / "dist", mixed)
    shutil.rmtree(mixed / "ia-taxonomy")
    shutil.copytree(checkout.parent / "base" / "ia-taxonomy", mixed / "ia-taxonomy")
    errors = distverify.set_errors(mixed, sorted(p.name for p in mixed.iterdir()))
    assert any(e.startswith("KN-005:") for e in errors), errors


@pytest.mark.parametrize("change", ["edit", "extra", "remove_dependency", "manifest"])
def test_installed_distribution_check(checkout, tmp_path, change):
    installed = tmp_path / "installed skills"
    shutil.copytree(checkout.parent / "base", installed)
    if change == "edit":
        (installed / "ia-taxonomy/SKILL.md").write_text("changed", encoding="utf-8")
    elif change == "extra":
        (installed / "ia-taxonomy/templates/old.md").write_text("old", encoding="utf-8")
    elif change == "remove_dependency":
        shutil.rmtree(installed / "ia-structure")
    else:
        doc = yamlio.load(installed / "ia-taxonomy" / distverify.MANIFEST_NAME)
        doc["files"] = doc["files"][1:]
        (installed / "ia-taxonomy" / distverify.MANIFEST_NAME).write_text(yamlio.dumps(doc), encoding="utf-8")
    errors = distverify.set_errors(installed, sorted(p.name for p in installed.iterdir()))
    assert errors, change
    if change == "remove_dependency":
        assert errors == ["ia-orchestrator: 依存Skill ia-structure が配置されていない"]


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
