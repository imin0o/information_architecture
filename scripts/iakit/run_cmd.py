"""成果物登録・stale化（4.4、FR-08、IN-02、AC-12）。実行ディレクトリの作成は run_init、記録の読み書きは run_store。

ハッシュ・版・依存・機密区分の継承は手で書かず、このコマンドで更新する。判断と指摘は iakit decision・
iakit finding で書き、承認・送信の記録はエージェントが YAML に書いて iakit contracts check で検査する。
"""

from pathlib import Path

from . import artifact_checks, decision_build, manifest, measure, run_outputs, runs, stamps, yamlio
from .decisions import artifact_inputs, latest_active, max_classification
# init・skill_item などは呼出側（cli_run、テスト）の互換のため run_cmd からも参照できるようにする
from .run_init import CONTRACT_VERSION, LATER_ROUTES, STAGE_SKILLS, init, skill_item  # noqa: F401
from .run_store import load, now, save

YAML_KINDS = set(artifact_checks.YAML_SCHEMAS)
EVIDENCE_KINDS = {"ia_review", "review_findings"}


def _current_hashes(run: dict, inputs: dict) -> dict[str, str]:
    found = {i["input_id"]: i["sha256"] for i in inputs["inputs"]}
    found.update({a["artifact_id"]: a["sha256"] for a in run["artifacts"]})
    return found


def refresh(directory: Path, changed_inputs: tuple[str, ...] = ()) -> dict:
    """inputs.yaml のハッシュと判断参照を更新し、依存が変わった成果物を連鎖的に stale にする。"""
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")
    decisions = load(directory, "decisions.yaml")["decisions"]
    sha = manifest.sha256_file(directory / "inputs.yaml")
    if sha != run["inputs_sha256"]:
        run["inputs_sha256"] = sha
        if run["artifacts"]:
            # 成果物を作り始めた後の入力変更だけを履歴に残す。開始時の登録は started に含める
            run["history"].append({"event": "input_changed", "at": now(), "note": "入力登録簿を更新した"})
    current, staled, changed = _current_hashes(run, inputs), [], True
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    active = latest_active(decisions)
    studies = yamlio.load(directory / "observations.yaml")["studies"] if (directory / "observations.yaml").is_file() else []
    for a in (a for a in run["artifacts"] if a["status"] == "current"):
        old = next((r for r in a["decision_refs"] if (r["decision_id"], r["decision_revision"]) not in active), None)
        if old:
            # 回答の追加などで判断が置き換わった。旧版に基づく成果物と、その後続を使わない
            a.update(status="stale", stale_reason=f"判断 {old['decision_id']} r{old['decision_revision']} が現行でなくなった。"
                                                  "現行の判断で作り直すまで使わない")
            staled.append(a["artifact_id"])
        else:
            required, _ = artifact_inputs(a["decision_refs"], decisions, studies)
            required |= runs.evaluation_dependencies(a["kind"], run["artifacts"])
            missing = sorted(required - {d["ref"] for d in a["depends_on"]})
            if missing:
                a.update(status="stale", stale_reason=f"判断・評価対象の依存 {missing} の記録がない。確認して再登録する")
                staled.append(a["artifact_id"])
    while changed:
        changed = False
        for a in (a for a in run["artifacts"] if a["status"] == "current"):
            for dep in a["depends_on"]:
                ref = dep["ref"]
                if ref in changed_inputs or current.get(ref) != dep["sha256"] or arts.get(ref, {}).get("status") == "stale":
                    a.update(status="stale", stale_reason=f"依存 {ref} が変わった。再生成か影響なしの確認まで使わない")
                    staled.append(a["artifact_id"])
                    changed = True
                    break
    if staled and run["status"] == "completed":
        run["status"] = "in_progress"
    run["decision_refs"] = [{"decision_id": d["decision_id"], "decision_revision": d["decision_revision"]}
                            for d in decisions]
    if (directory / "findings.yaml").is_file():
        found = yamlio.load(directory / "findings.yaml")["findings"]
        run["refine"]["unresolved_critical"] = runs.unresolved_critical(found)
    save(directory, "run.yaml", run)
    return {"inputs_sha256": sha, "staled": staled}


def refresh_validation(directory: Path, previous: dict, revised: dict) -> None:
    """設計を変えない検証情報の改訂だけを反映する。本文の参照・依存・区分を更新し、成果物の版は保つ。

    呼出側は判断の設計内容が同一であることと、書込み全体のロールバックを保証する。
    既に stale の成果物や、検証情報以外で変わった依存は更新済みとして扱わない。
    """
    run, inputs_doc = load(directory, "run.yaml"), load(directory, "inputs.yaml")
    decisions = load(directory, "decisions.yaml")["decisions"]
    studies = yamlio.load(directory / "observations.yaml")["studies"] if (directory / "observations.yaml").is_file() else []
    inputs = {i["input_id"]: i for i in inputs_doc["inputs"]}
    arts = {a["artifact_id"]: a for a in run["artifacts"]}
    previous_hashes = {aid: a["sha256"] for aid, a in arts.items()}
    pending = {aid for aid, a in arts.items() if a["status"] == "current"}
    updated = set()
    while pending:
        ready = [aid for aid in sorted(pending)
                 if not any(d["ref"] in pending for d in arts[aid]["depends_on"])]
        if not ready:
            raise ValueError("検証情報を反映できない: 成果物の依存が循環している")
        for aid in ready:
            pending.remove(aid)
            a = arts[aid]
            refs = [{**r, "decision_revision": revised["decision_revision"]}
                    if (r["decision_id"], r["decision_revision"]) == (
                        previous["decision_id"], previous["decision_revision"]) else r
                    for r in a["decision_refs"]]
            if refs == a["decision_refs"] and not any(d["ref"] in updated for d in a["depends_on"]):
                continue
            path = directory / a["path"]
            if manifest.sha256_file(path) != a["sha256"]:
                raise ValueError(f"{aid}: 本文が登録時と異なるため、検証情報だけの更新はできない")
            required, decision_levels = artifact_inputs(refs, decisions, studies)
            dependencies = {d["ref"]: dict(d) for d in a["depends_on"]}
            for ref in required - dependencies.keys():
                dependencies[ref] = {"ref": ref, "sha256": inputs[ref]["sha256"]}
            for ref in dependencies.keys() & updated:
                if dependencies[ref]["sha256"] == previous_hashes[ref]:
                    dependencies[ref]["sha256"] = arts[ref]["sha256"]
            levels = [v["classification"] for ref in dependencies if (v := inputs.get(ref) or arts.get(ref))]
            level = a["classification"] if "classification_override" in a else max_classification(
                [a["classification"], *levels, *decision_levels])
            text = path.read_bytes().decode("utf-8")
            if a["kind"] != "review_findings":
                text = stamps.rewrite_decisions(text, [dict(previous, status="superseded"), revised],
                                                markdown=a["kind"] not in YAML_KINDS)
            own = {"artifact_id": aid, "version": a["version"], "classification": level}
            text = stamps.rewrite(text, a["kind"] not in YAML_KINDS, own, {})
            path.write_text(text, encoding="utf-8", newline="")
            a.update(sha256=manifest.sha256_file(path), classification=level, decision_refs=refs,
                     depends_on=[dependencies[ref] for ref in sorted(dependencies)])
            updated.add(aid)
    sim_path = directory / "simulations.yaml"
    if sim_path.is_file():
        doc = yamlio.load(sim_path)
        changed = False
        for sim in doc["simulations"]:
            refs = [sim["target"], sim["persona"]["source"]]
            levels = [arts[r["artifact_id"]]["classification"] for r in refs
                      if r["artifact_id"] in updated and arts[r["artifact_id"]]["version"] == r["version"]]
            level = max_classification([sim["classification"], *levels])
            if level != sim["classification"]:
                sim["classification"], changed = level, True
        if changed:
            sim_path.write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")
    measure.refresh_sources(directory, run, previous_hashes, run_outputs.validation_plan.measurement_paths(directory, run))
    save(directory, "run.yaml", run)


def artifact(directory: Path, rel: str, kind: str, depends: list[str], decision_refs: list[str],
             no_impact: str | None = None) -> dict:
    """成果物を登録する。本体の版・機密区分と、依存先の参照版（評価の記録を除く）はこのコマンドが書き換える。

    no_impact は stale の成果物を内容を変えずに再登録するときの確認の理由で、判断として記録する。
    """
    if kind not in YAML_KINDS | run_outputs.MARKDOWN_KINDS:
        raise ValueError(f"未知の成果物の型 {kind}")
    run, inputs = load(directory, "run.yaml"), load(directory, "inputs.yaml")
    if not (directory / rel).is_file() and not Path(rel).is_file():
        raise ValueError(f"{rel} がない")
    rel = stamps.run_relative(directory, rel)
    path = directory / rel
    text = path.read_text(encoding="utf-8")
    body = yamlio.load(path) if kind in YAML_KINDS else None
    existing = next((a for a in run["artifacts"] if a["kind"] == kind), None)
    sha = manifest.sha256_file(path)
    if existing and existing["path"] != rel:
        raise ValueError(f"{kind} は {existing['path']} で登録済み。同じ型の成果物は1つにする")
    unchanged = bool(existing) and existing["sha256"] == sha
    if no_impact and not (existing and existing["status"] == "stale"):
        raise ValueError("--no-impact は stale の成果物を再登録するときだけ使う")
    if existing and existing["status"] == "stale" and unchanged and not no_impact:
        raise ValueError(f"{existing['artifact_id']} は stale で、内容が登録時と同じ。作り直すか、"
                         f"影響がないことを確かめて --no-impact <理由> で再登録する（理由は判断として記録する）")
    if existing:
        version = existing["version"] if unchanged and existing["status"] == "current" else existing["version"] + 1
        aid = existing["artifact_id"]
    else:
        version = 1
        aid = f"ART-{len(run['artifacts']) + 1:03d}"
    original = text
    artifact_checks.schema_check(kind, body, {"artifact_id": aid, "version": version, "classification": "public"})
    decisions = load(directory, "decisions.yaml")["decisions"]
    if kind != "review_findings":
        # 置き換えられた判断の参照は現行版へ書き換える。指摘の basis は評価時点の証跡なので対象外
        text = stamps.rewrite_decisions(text, decisions, markdown=kind not in YAML_KINDS)
        body = yamlio.loads(text) if kind in YAML_KINDS else None
    artifact_checks.ref_kind_check(kind, body, run["artifacts"])
    refs, found = artifact_checks.body_refs(kind, body, text)
    explicit = [(r.split(":")[0], int(r.split(":")[1])) for r in decision_refs]
    decision_items = [{"decision_id": d, "decision_revision": r} for d, r in sorted(set(explicit + found))]
    active = latest_active(decisions)
    if any((r["decision_id"], r["decision_revision"]) not in active for r in decision_items):
        raise ValueError("登録する成果物が未知または現行でない判断を参照している")
    studies = yamlio.load(directory / "observations.yaml")["studies"] if (directory / "observations.yaml").is_file() else []
    indirect, decision_levels = artifact_inputs(decision_items, decisions, studies)
    refs.update(indirect)
    refs.update(runs.evaluation_dependencies(kind, run["artifacts"]))
    if kind == "sitemap":
        refs.update(a["artifact_id"] for a in run["artifacts"] if a["kind"] == "taxonomy")
        if any(n["concept_id"] for n in body["nodes"]):
            refs.update(a["artifact_id"] for a in run["artifacts"] if a["kind"] == "controlled_vocabulary")
    refs.update(depends)
    if aid in refs:
        raise ValueError(f"{aid} は登録する成果物自身のIDで、依存にできない")
    current = _current_hashes(run, inputs)
    unknown = sorted(r for r in refs if r not in current)
    if unknown:
        raise ValueError(f"未登録の依存 {unknown}")
    levels = {i["input_id"]: i["classification"] for i in inputs["inputs"]}
    levels.update({a["artifact_id"]: a["classification"] for a in run["artifacts"]})
    level = max_classification([levels[r] for r in refs] + decision_levels)
    if existing and "classification_override" in existing:
        level = existing["classification"]
    own = {"artifact_id": aid, "version": version, "classification": level}
    # 評価の記録が示す対象の版は評価した時点の証跡で、現行版へ書き換えない
    versions = {} if kind in EVIDENCE_KINDS else {a["artifact_id"]: a["version"] for a in run["artifacts"]}
    stamped = stamps.rewrite(text, kind not in YAML_KINDS,
                             own if body is None or (isinstance(body, dict) and "artifact_id" in body) else None,
                             versions)
    saved = {p: p.read_bytes() for p in (path, directory / "run.yaml", directory / "decisions.yaml")}
    try:
        if stamped != original:
            path.write_text(stamped, encoding="utf-8", newline="")
            sha = manifest.sha256_file(path)
        body = yamlio.load(path) if kind in YAML_KINDS else None
        artifact_checks.schema_check(kind, body)
        if body is not None and isinstance(body, dict) and "artifact_id" in body:
            wrong = {k: v for k, v in own.items() if body.get(k) != v}
            if wrong:
                raise ValueError(f"{rel}: 本体の {', '.join(f'{k} を {v}' for k, v in wrong.items())} に書き換えられない。"
                                 "最上位の行に artifact_id・version・classification を1行ずつ書く")
        entry = {
            "artifact_id": aid, "kind": kind, "path": rel, "version": version, "sha256": sha, "status": "current",
            "stale_reason": None, "classification": level,
            "depends_on": [{"ref": r, "sha256": current[r]} for r in sorted(refs)],
            "decision_refs": decision_items,
        }
        if existing and "classification_override" in existing:
            entry["classification_override"] = existing["classification_override"]
        artifact_checks.contract_check(directory, run, entry, decisions)
        out = {"artifact_id": aid, "version": version, "classification": level}
        if no_impact:
            doc = load(directory, "decisions.yaml")
            record = decision_build.no_impact(run, doc["decisions"], existing, no_impact, now())
            doc["decisions"].append(record)
            save(directory, "decisions.yaml", doc)
            out["no_impact_decision"] = f"{record['decision_id']} r1"
        if existing:
            run["artifacts"][run["artifacts"].index(existing)] = entry
        else:
            run["artifacts"].append(entry)
        save(directory, "run.yaml", run)
        out["staled"] = refresh(directory)["staled"]
    except Exception:
        # 本文・影響なしの判断・登録簿の更新を一体として戻す。保存やrefreshの失敗も含む。
        for target, data in saved.items():
            if target.read_bytes() != data:
                target.write_bytes(data)
        raise
    if stamped != original:
        out["rewritten"] = True
    if kind == "ia_review":
        # 評価対象の版は書き換えない。再評価で現行版と違うまま登録した場合に気付けるよう警告する
        now_versions = {a["artifact_id"]: a["version"] for a in run["artifacts"]}
        old = sorted({f"{m.group(1)} v{m.group(2)}（現行 v{now_versions[m.group(1)]}）"
                      for m in stamps._MD_REF.finditer(stamped[:stamps._header_end(stamped)])
                      if m.group(1) != aid and now_versions.get(m.group(1), int(m.group(2))) != int(m.group(2))})
        if old:
            out["warnings"] = [f"評価の記録の冒頭が現行でない版を評価対象に挙げている: {', '.join(old)}"]
    return out
