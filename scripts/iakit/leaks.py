"""原典・機密入力をindex、コミット、配布前ディレクトリで検査する。"""

import subprocess
from pathlib import Path

from . import leak_sources, yamlio
from .leak_rules import inspect
from .paths import ROOT


def check(root: Path = ROOT, staged_only: bool = False, dirs: list[Path] | None = None,
          revisions: list[str] | None = None, baseline: tuple | None = None) -> dict:
    violations, errors, labels = [], [], []
    scanned, known = 0, set()
    if dirs:
        targets = [(f"dir:{d.as_posix()}", d, None) for d in dirs]
    elif revisions is not None:
        targets = [(f"git:commit:{rev}", None, rev) for rev in revisions]
    else:
        targets = [("git:staged" if staged_only else "git:index", None, None)]
    for label, directory, revision in targets:
        labels.append(label)
        try:
            if directory is not None:
                if not directory.is_dir():
                    raise yamlio.DataError("指定した検査対象ディレクトリが存在しない")
                paths = [p for p in directory.rglob("*") if p.is_file()]
                if not paths:
                    raise yamlio.DataError("指定した検査対象ディレクトリが空")
                read = lambda path: (root / path).read_bytes()
            else:
                paths = leak_sources.git_paths(root, staged_only, revision)
                read = lambda path: leak_sources.read_git(root, path, revision)
            legacy = revision and baseline and not {
                leak_sources.MANIFEST_PATH, leak_sources.FIXTURE_PATH
            }.intersection(paths)
            originals, fixtures = (baseline[0], {}) if legacy else leak_sources.policies(read)
            if baseline:
                originals = originals | baseline[0]
            known.update(originals)
            for path in paths:
                if directory is not None:
                    rel, content = path.relative_to(directory).as_posix(), path.read_bytes()
                else:
                    rel, content = path, read(path)
                scanned += 1
                for reason in inspect(rel, content, originals, fixtures):
                    violations.append({"target": label, "path": rel, "reason": reason})
        except (yamlio.DataError, OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            reason = str(exc) if isinstance(exc, yamlio.DataError) else "検査対象の読込みに失敗"
            errors.append({"target": label, "reason": reason})
    return {
        "check": "leaks",
        "result": "fail" if violations or errors else "pass",
        "targets": labels,
        "files_scanned": scanned,
        "known_original_hashes": len(known),
        "violations": violations,
        "errors": errors,
    }
