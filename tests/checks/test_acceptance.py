"""M6の受け入れ判定一覧（library/build-logs/checks/m6-acceptance.yaml）の形式と参照先（要件10章、AC-01〜12）。

判定の中身は人・確認エージェントのレビューと各検査記録で決める。ここでは、全ACがそろい、合否の値が決まった
語彙で、Git管理する根拠ファイルが実在し、未完了の項目に未実施事項か残件が書かれていることだけを確かめる。
"""

from iakit import yamlio
from iakit.paths import ROOT

RECORD = ROOT / "library" / "build-logs" / "checks" / "m6-acceptance.yaml"
RESULTS = {"pass", "fail", "incomplete"}


def test_acceptance_record_covers_every_criterion_with_existing_evidence():
    doc = yamlio.load(RECORD)
    ids = [c["id"] for c in doc["criteria"]]
    assert ids == [f"AC-{n:02d}" for n in range(1, 13)]
    for c in doc["criteria"]:
        assert c["result"] in RESULTS, c["id"]
        assert c["evidence"], c["id"]
        for ref in c["evidence"]:
            path = ref.split("#", 1)[0]
            # runs/ は本文を含む実行結果でGit管理外。参照だけを残す
            assert path.startswith("runs/") or (ROOT / path).exists(), f"{c['id']}: {ref}"
        if c["result"] != "pass":
            assert c.get("not_run") or c.get("remaining"), c["id"]
    assert set(doc["candidate"]["distribution_ids"]) == {
        "ia-analysis", "ia-evaluation", "ia-orchestrator", "ia-structure", "ia-taxonomy"}
