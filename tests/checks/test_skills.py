"""MVPの5つのSkillのソース（BR-03、NFR-06、FR-08）。内容の良否は人の評価で判定する。"""

import re

import pytest

from iakit import run_outputs, runs, yamlio
from iakit.paths import ROOT

SKILLS = ROOT / "skills"
MVP = ["ia-orchestrator", "ia-analysis", "ia-taxonomy", "ia-structure", "ia-evaluation"]
LINK = re.compile(r"\]\(([^)#]+)(?:#[^)]*)?\)")


def front(name):
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yamlio.loads(text.split("---", 2)[1]), text


@pytest.mark.parametrize("name", MVP)
def test_skill_metadata_and_size(name):
    meta, text = front(name)
    assert meta["name"] == name
    assert 0 < len(meta["description"]) <= 1024
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", meta["metadata"]["version"])
    assert len(text.splitlines()) <= 500
    # 特定ホスト専用の変数・動的注入を使わない
    assert not re.search(r"\$ARGUMENTS|\$\{CLAUDE|!`", text)


@pytest.mark.parametrize("name", MVP)
def test_relative_links_stay_inside_skill(name):
    base = SKILLS / name
    for path in base.rglob("*.md"):
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://")):
                continue
            resolved = (path.parent / target).resolve()
            assert resolved.is_file(), f"{path.relative_to(ROOT)}: {target} がない"
            assert resolved.is_relative_to(base.resolve()), f"{path.relative_to(ROOT)}: {target} がSkillの外を指す"


def test_knowledge_references_exist_and_exclude_book_notes():
    entries = {e["knowledge_id"]: e for e in yamlio.load(ROOT / "library/index.yaml")["entries"]}
    for name in MVP:
        for kid in set(re.findall(r"\bKN-[0-9]{3}\b", front(name)[1])):
            assert kid in entries, f"{name}: {kid} が索引にない"
            assert entries[kid]["kind"] != "book_note", f"{name}: 文献ノート {kid} を手順から参照しない"


def test_shared_references_are_identical_copies():
    records = {(SKILLS / n / "references/records.md").read_bytes() for n in MVP[1:]}
    assert len(records) == 1
    formats = {(SKILLS / n / "references/input-formats.md").read_bytes() for n in ("ia-orchestrator", "ia-analysis")}
    assert len(formats) == 1


def test_routing_matches_checked_routes_and_outputs():
    text = (SKILLS / "ia-orchestrator/routing.md").read_text(encoding="utf-8")
    labels = {"analyze": "Analyze", "classify": "Classify", "structure": "Structure", "validate": "Validate"}
    for task_type, stages in runs.ROUTES.items():
        row = next(line for line in text.splitlines() if f"（`{task_type}`）" in line)
        assert " → ".join(labels[s] for s in stages) in row and row.rstrip().endswith("MVP |")
    for kinds in run_outputs.STAGE_KINDS.values():
        for kind in kinds:
            assert f"`{kind}`" in text


@pytest.mark.parametrize("path", sorted(SKILLS.glob("*/templates/*.md")), ids=lambda p: p.name)
def test_templates_start_with_assumption_section(path):
    first = next(line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## "))
    assert first == run_outputs.ASSUMPTION_HEADING


@pytest.mark.parametrize("path", sorted(SKILLS.glob("*/templates/*.md")), ids=lambda p: p.name)
def test_markdown_contract_matches_templates(path):
    kind = path.stem.replace("-", "_")
    headings = [line[3:] for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## ")]
    assert headings == [run_outputs.ASSUMPTION_HEADING[3:], *run_outputs.MARKDOWN_HEADINGS[kind]]
