"""両ホストの配置先での受け入れ検査（M6、AC-10、BR-03）。ホストのモデルを使わない機械検査。

検査済みの dist/ を、ビルド元の外の一時ディレクトリへ各ホストの配置先（Claude Code: .claude/skills、
Codex: .agents/skills）として写し、SKILL.md が示すとおり `uv run <ia-orchestrator>/scripts/run_iakit.py` で
同梱の実行時処理を動かす。通常配置・異なる作業ディレクトリ・空白を含むパスで、標準経路の開始、入力登録、
契約検査、中断・再開、Refineの上限、未対応経路を確認し、依存欠落・不整合版・改変・共有IDの混在で
開始前に止まることを確認する。結果には本文を含めず、一時ディレクトリは終了時に削除する。
uv の実行ファイルは環境変数 IAKIT_UV で差し替えられる。
"""

import os
import platform
import shutil
import subprocess
import stat
import tempfile
import time
import uuid
from pathlib import Path

from . import distverify, yamlio
from .paths import ROOT

DIST = ROOT / "dist"
LAYOUTS = {"claude-code": ".claude/skills", "codex": ".agents/skills"}
VARIANTS = ("normal", "other_cwd", "space_in_path")
BRIEF = ROOT / "tests" / "cases" / "webia-001" / "inputs" / "brief.md"
FIXTURE_RUN = ROOT / "tests" / "fixtures" / "contracts" / "webia-run"


def tempdir(prefix: str) -> Path:
    """一時ディレクトリ。tempfile.mkdtemp は Windows で所有者だけのACLを付け、ホストのサンドボックス利用者
    （Codex）が作ったファイルを親から読めなくなるため、通常の権限を継承するディレクトリを作る。"""
    base = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex[:8]}"
    base.mkdir()
    return base


def remove(base: Path) -> str | None:
    """git init が作る読取専用ファイルも消す。消せなければ残った場所を返す（ホストのサンドボックスが作った権限など）。"""
    def retry(fn, path, _):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    for _ in range(10):  # ホストの子プロセス（MCPサーバーなど）が終了直後もファイルを掴んでいることがある
        try:
            shutil.rmtree(base, onexc=retry)
            return None
        except OSError:
            time.sleep(3)
    return str(base)


def manifest_files(skill: Path) -> set[str]:
    """配布時に manifest へ記録した同梱ファイルの一覧。"""
    doc = yamlio.load(skill / distverify.MANIFEST_NAME)
    return {f["path"] for f in doc["files"] + doc["schemas"]} | {distverify.MANIFEST_NAME}


def _uv() -> str:
    return os.environ.get("IAKIT_UV") or "uv"


def isolated_env() -> dict[str, str]:
    """ビルド元への手掛かりを除いた環境変数。uv run で起動すると VIRTUAL_ENV と .venv の Scripts がPATHに入り、
    配置先のホストが `uv run iakit` でビルド元のツールと skills/ を使えてしまう（M6で検出）。"""
    root = str(ROOT).lower()
    env = {k: v for k, v in os.environ.items()
           if k != "VIRTUAL_ENV" and not k.startswith("UV_RUN") and (k == "PATH" or root not in v.lower())}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if root not in p.lower())
    return env


def _call(skills: Path, cwd: Path, base: Path, *args) -> tuple[int, str]:
    runner = skills / "ia-orchestrator" / "scripts" / "run_iakit.py"
    cmd = [_uv(), "run", "--no-project", "--quiet", str(runner), *map(str, args)]
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=300, env=isolated_env())
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, type(exc).__name__
    text = (done.stdout + done.stderr).replace(str(base), "<base>")
    return done.returncode, text


def _place(base: Path, layout: str, names: list[str] | None = None) -> tuple[Path, Path]:
    workspace = base / "workspace"
    skills = workspace / LAYOUTS[layout]
    for name in names or sorted(p.name for p in DIST.iterdir() if (p / "SKILL.md").is_file()):
        shutil.copytree(DIST / name, skills / name)
    return workspace, skills


def _record(checks: list, name: str, expect: str, code: int, text: str, needle: str = "") -> None:
    ok = code == 0 if expect == "pass" else code != 0 and needle in text
    entry = {"name": name, "expect": expect, "exit_code": code, "result": "pass" if ok else "fail"}
    if code != 0 or not ok:
        entry["detail"] = next((ln.strip()[:200] for ln in text.splitlines() if "error" in ln or "失敗" in ln),
                               text.strip()[:200])
    checks.append(entry)


def _flow(skills: Path, workspace: Path, base: Path, variant: str) -> list[dict]:
    """標準経路の開始から再開・Refineの上限までを1つの配置で確認する。"""
    cwd = base / "elsewhere" if variant == "other_cwd" else workspace
    cwd.mkdir(exist_ok=True)
    runs = workspace / "runs" if variant == "other_cwd" else Path("runs")
    brief = workspace / "input files" / "brief.md"
    brief.parent.mkdir(exist_ok=True)
    shutil.copyfile(BRIEF, brief)
    checks: list[dict] = []
    call = lambda *a: _call(skills, cwd, base, *a)  # noqa: E731
    common = ("--host", "other", "--model", "none")
    _record(checks, "placement_integrity", "pass", len(distverify.set_errors(skills, sorted(
        p.name for p in skills.iterdir()))), "")
    for task in ("web_ia", "taxonomy"):
        run = runs / f"RUN-{task.upper().replace('_', '')}-M6"
        _record(checks, f"init_{task}", "pass", *call("run", "init", run, "--run-id", run.name,
                                                      "--task-type", task, *common))
    run = runs / "RUN-WEBIA-M6"
    _record(checks, "input_add", "pass", *call("input", "add", run, brief, "--kind", "brief", "--description", "m6"))
    _record(checks, "contracts_check", "pass", *call("contracts", "check", run))
    _record(checks, "interrupt", "pass", *call("run", "event", run, "interrupted", "--note", "m6"))
    _record(checks, "resume_unchanged", "pass", *call("run", "event", run, "resumed"))
    _record(checks, "interrupt_again", "pass", *call("run", "event", run, "interrupted", "--note", "m6"))
    stored = workspace / "runs" / run.name / "inputs" / "brief.md"
    stored.write_text(stored.read_text(encoding="utf-8") + "\n変更\n", encoding="utf-8")
    _record(checks, "resume_changed_input", "stop", *call("run", "event", run, "resumed"), "実体が登録時と異なる")
    tax = runs / "RUN-TAXONOMY-M6"
    for i in (1, 2):
        _record(checks, f"refine_{i}", "pass", *call("run", "event", tax, "refine", "--note", "m6"))
    _record(checks, "refine_3", "stop", *call("run", "event", tax, "refine", "--note", "m6"), "2巡まで")
    code, text = call("run", "init", runs / "RUN-CORP-M6", "--run-id", "RUN-CORP-M6",
                      "--task-type", "corporate_document", *common)
    _record(checks, "unsupported_route", "pass", code if "supported: false" in text else 1, text)
    return checks


def _reseal(skill: Path) -> None:
    """別のライブラリ版から作った配布物を模擬する。知識の内容ハッシュを1件変え、配布IDを付け直す。"""
    path = skill / distverify.MANIFEST_NAME
    doc = yamlio.load(path)
    doc["knowledge"][0]["sha256"] = "0" * 64
    doc["distribution_id"] = distverify.distribution_id(doc)
    path.write_text(yamlio.dumps(doc), encoding="utf-8", newline="\n")


def _negatives(base: Path, layout: str) -> list[dict]:
    checks: list[dict] = []
    common = ("--host", "other", "--model", "none")

    def case(label: str, mutate, task: str, expect: str, needle: str = "") -> None:
        root = base / label
        workspace, skills = _place(root, layout)
        mutate(skills)
        run = Path("runs") / f"RUN-{label.upper().replace('_', '-')}"
        code, text = _call(skills, workspace, base, "run", "init", run, "--run-id", run.name,
                           "--task-type", task, *common)
        _record(checks, f"{label}:{task}", expect, code, text, needle)

    drop = lambda s: shutil.rmtree(s / "ia-structure")  # noqa: E731
    case("missing_dependency", drop, "web_ia", "stop", "必要なSkillがない")
    case("missing_unneeded", drop, "taxonomy", "pass")

    def contract(s: Path) -> None:
        md = s / "ia-analysis" / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8").replace('contract_version: "1.0"', 'contract_version: "2.0"'),
                      encoding="utf-8")
    case("incompatible_contract", contract, "taxonomy", "stop", "非互換")

    def modify(s: Path) -> None:
        md = s / "ia-taxonomy" / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    case("modified_file", modify, "taxonomy", "stop", "配布物の検査に失敗")
    case("mixed_shared_id", lambda s: _reseal(s / "ia-taxonomy"), "taxonomy", "stop", "内容が異なる")
    _, alone = _place(base / "standalone", layout, ["ia-taxonomy"])
    _record(checks, "standalone_domain_skill", "pass", len(distverify.set_errors(alone, ["ia-taxonomy"])), "")
    out = base / "standalone out"
    out.mkdir()
    for name in ("controlled-vocabulary.yaml", "taxonomy.yaml"):
        shutil.copyfile(FIXTURE_RUN / name, out / name)
    script = alone / "ia-taxonomy" / "scripts" / "check_artifacts.py"

    def schema_check() -> tuple[int, str]:  # 単独利用の形式検査（P-M6-01）
        done = subprocess.run([_uv(), "run", "--no-project", "--quiet", str(script), str(out)], cwd=out,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                              env=isolated_env())
        return done.returncode, done.stdout + done.stderr
    _record(checks, "standalone_schema_check", "pass", *schema_check())
    (out / "taxonomy.yaml").write_text("schema_version: '1.0'\n", encoding="utf-8")
    _record(checks, "standalone_schema_check_broken", "stop", *schema_check(), "result: fail")
    return checks


def placement() -> dict:
    if not any(DIST.glob("*/build-manifest.yaml")):
        return {"check": "accept_placement", "result": "fail", "errors": ["dist/ がない。iakit dist build を先に実行する"]}
    code = subprocess.run([_uv(), "--version"], capture_output=True, text=True).stdout.strip()
    ids = {p.parent.name: yamlio.load(p)["distribution_id"] for p in sorted(DIST.glob("*/build-manifest.yaml"))}
    recorded = {d["skill"]: d["distribution_id"]
                for d in yamlio.load(ROOT / "verification" / "distributions.yaml")["distributions"]}
    out = {"check": "accept_placement", "os": f"{platform.system()} {platform.release()}", "uv": code,
           "runner": "uv run --no-project <ia-orchestrator>/scripts/run_iakit.py",
           "distribution_ids": ids, "matches_record": ids == recorded, "placements": []}
    for layout in LAYOUTS:
        for variant in VARIANTS:
            base = Path(tempfile.mkdtemp(prefix="iakit m6 " if variant == "space_in_path" else "iakit-m6-"))
            try:
                workspace, skills = _place(base, layout)
                out["placements"].append({"host_layout": layout, "placement": f"<workspace>/{LAYOUTS[layout]}",
                                          "variant": variant, "checks": _flow(skills, workspace, base, variant)})
            finally:
                shutil.rmtree(base, ignore_errors=True)
        base = Path(tempfile.mkdtemp(prefix="iakit-m6-neg-"))
        try:
            out["placements"].append({"host_layout": layout, "placement": f"<workspace>/{LAYOUTS[layout]}",
                                      "variant": "negative", "checks": _negatives(base, layout)})
        finally:
            shutil.rmtree(base, ignore_errors=True)
    failed = [f"{p['host_layout']}/{p['variant']}/{c['name']}"
              for p in out["placements"] for c in p["checks"] if c["result"] != "pass"]
    out["result"] = "pass" if out["matches_record"] and not failed else "fail"
    if failed or not out["matches_record"]:
        out["errors"] = failed + ([] if out["matches_record"] else ["dist/ の配布IDが verification/distributions.yaml と異なる"])
    return out
