import pytest

from iakit import cli, paths


def test_cli_explains_checkout_requirement(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.main(["manifest", "check"])
    assert exc.value.code == 2
    assert "checkout" in capsys.readouterr().err


def test_leak_modes_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["leaks", "check", "--staged", "--dir", "dist"])
