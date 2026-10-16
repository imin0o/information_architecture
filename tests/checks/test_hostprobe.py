"""ホスト配置確認の判定（M1）。実ホストは呼ばず、付属資料を読む偽ホストで確かめる。"""

import sys

from iakit import hostprobe

READS_REFERENCE = (
    "import pathlib\n"
    "p = pathlib.Path('.fake/skills/iakit-host-probe/references/token.md')\n"
    "print('PROBE-RESULT: ' + p.read_text(encoding='utf-8').split('`')[1] if p.exists() else 'no skill')\n"
)


def fake(monkeypatch, script, version_ok=True):
    monkeypatch.setitem(hostprobe.HOSTS, "fake", {
        "skills_dir": ".fake/skills",
        "bin": sys.executable,
        "version": ["-c", "print('fake 1.0')" if version_ok else "raise SystemExit(1)"],
        "command": lambda cwd, prompt: ["-c", script],
        "explicit": "use it",
    })


def test_probe_passes_when_reference_is_read_and_control_has_no_token(monkeypatch):
    fake(monkeypatch, READS_REFERENCE)
    result = hostprobe.probe("fake", space_in_path=True)
    assert result["result"] == "pass", result
    assert result["checks"]["control"]["token_returned"] is False


def test_token_without_skill_fails_control(monkeypatch):
    fake(monkeypatch, f"print('PROBE-RESULT: {hostprobe.TOKEN}')")
    assert hostprobe.probe("fake")["checks"]["control"]["result"] == "fail"


def test_unavailable_host_is_blocked_not_passed(monkeypatch):
    fake(monkeypatch, READS_REFERENCE, version_ok=False)
    assert hostprobe.probe("fake")["result"] == "blocked"


def test_token_is_only_in_the_reference_file():
    assert hostprobe.TOKEN not in (hostprobe.PROBE / "SKILL.md").read_text(encoding="utf-8")
    assert hostprobe.TOKEN in (hostprobe.PROBE / "references/token.md").read_text(encoding="utf-8")
