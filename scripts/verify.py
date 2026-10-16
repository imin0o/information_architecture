"""Python両版と全候補ファイルを、実indexを変更せず検証するrunner。"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from iakit import yamlio

ROOT = Path(__file__).resolve().parents[1]


def run(*args, env=None, capture=False):
    process = subprocess.run(args, cwd=ROOT, env=env, capture_output=capture)
    if process.returncode and capture:
        sys.stdout.buffer.write(process.stdout)
        sys.stderr.buffer.write(process.stderr)
    process.check_returncode()
    return process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="iakit-verify-") as temp:
        scratch = Path(temp)
        for version in ("3.12", "3.14"):
            env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(scratch / "venv"), PYTHONUTF8="1")
            env.pop("VIRTUAL_ENV", None)
            run("uv", "sync", "--locked", "--python", version, env=env)
            actual = run("uv", "run", "--locked", "--python", version, "python", "--version", env=env, capture=True)
            junit = scratch / "pytest.xml"
            run("uv", "run", "--locked", "--python", version, "pytest", "-q", "--basetemp", str(scratch / "pytest"),
                "--junitxml", str(junit), env=env)
            suites = ET.parse(junit).getroot().findall("testsuite")
            results.append({
                "python": actual.stdout.decode().strip(), "result": "pass",
                "tests": sum(int(s.get("tests", 0)) for s in suites),
            })
        # 未追跡のM0成果物も含める。原典等は作業ツリーの.gitignoreで除外される。
        env = dict(os.environ, GIT_INDEX_FILE=str(scratch / "index"), PYTHONUTF8="1")
        run("git", "read-tree", "HEAD", env=env)
        run("git", "add", "--all", "--", ".", env=env)
        checked = run("uv", "run", "--locked", "iakit", "check", "--local", env=env, capture=True)
        checks = yamlio.loads(checked.stdout.decode("utf-8"))
        uv = run("uv", "--version", capture=True).stdout.decode().strip()
        result = {
            "schema_version": "0.1.0", "result": "pass", "uv": uv,
            "tests": results, "candidate_snapshot": "temporary index; actual index unchanged",
            "checks": checks["checks"],
        }
        text = yamlio.dumps(result)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(text, encoding="utf-8", newline="\n")
        print(text)


if __name__ == "__main__":
    main()
