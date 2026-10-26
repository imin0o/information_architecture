"""個人用ルートへの写し直し（scripts/sync_global_skills.py）。iakit は差し替え、写し直すかの判定だけを見る。"""

import importlib.util
import subprocess

import pytest

from iakit.paths import ROOT

_spec = importlib.util.spec_from_file_location("sync_global_skills", ROOT / "scripts" / "sync_global_skills.py")
sync_global_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_global_skills)


def place(root, name, version):
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(version, encoding="utf-8")
    (skill / "build-manifest.yaml").write_text(f"version: {version}\n", encoding="utf-8")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """dist/ に新しい一式を置き、iakit の dist check は常に通す（古い一式も内部では整合している）。"""
    dist, target = tmp_path / "dist", tmp_path / "skills"
    for name in ("ia-analysis", "ia-taxonomy"):
        place(dist, name, "new")
    target.mkdir()
    monkeypatch.setattr(sync_global_skills, "DIST", dist)
    monkeypatch.setattr(sync_global_skills, "TARGETS", (target,))
    monkeypatch.setattr(sync_global_skills, "iakit", lambda *args: subprocess.CompletedProcess(args, 0, "", ""))
    return dist, target


@pytest.mark.parametrize("placed", [("new", "old"), ("new",)], ids=["stale", "missing"])
def test_internally_consistent_stale_set_is_replaced(roots, capsys, placed):
    _, target = roots
    for name, version in zip(("ia-analysis", "ia-taxonomy"), placed):
        place(target, name, version)
    assert sync_global_skills.main([]) == 0
    assert capsys.readouterr().out.startswith("写し直し:")
    for name in ("ia-analysis", "ia-taxonomy"):
        assert (target / name / "SKILL.md").read_text(encoding="utf-8") == "new"


def test_set_matching_dist_is_left_alone(roots, capsys):
    _, target = roots
    for name in ("ia-analysis", "ia-taxonomy"):
        place(target, name, "new")
    (target / "ia-analysis" / "kept").write_text("", encoding="utf-8")
    assert sync_global_skills.main([]) == 0
    assert capsys.readouterr().out.startswith("一致済み:")
    assert (target / "ia-analysis" / "kept").is_file()


def test_set_still_differing_after_copy_fails(roots, capsys, monkeypatch):
    _, target = roots
    for name in ("ia-analysis", "ia-taxonomy"):
        place(target, name, "old")
    monkeypatch.setattr(sync_global_skills, "sync", lambda target: None)
    assert sync_global_skills.main([]) == 2
    assert "dist/ と一致しません" in capsys.readouterr().err
