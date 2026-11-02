"""One shared CLI parser keeps expected errors distinct from unexpected failures."""
from __future__ import annotations

import pytest

from poolhouse.command import Group


def test_declared_error_is_reported_once_and_credentials_are_redacted(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("EXAMPLE_API_TOKEN", "private-example-credential")

    def run(args):
        calls.append(args)
        raise OSError("unavailable private-example-credential")

    command = Group("example", "Example", run=run, errors=(OSError,))
    assert command.run([]) == 2
    assert len(calls) == 1
    assert "private-example-credential" not in capsys.readouterr().err


@pytest.mark.parametrize("errors", [(), (ValueError,)])
def test_undeclared_errors_propagate(errors):
    def run(args):
        raise OSError("unavailable")

    with pytest.raises(OSError, match="unavailable"):
        Group("example", "Example", run=run, errors=errors).run([])


def test_help_and_bad_arguments_never_call_the_handler(capsys):
    def run(args):
        raise AssertionError("parser invoked the handler")

    command = Group("example", "Example", run=run, errors=(Exception,))
    for argv, expected in ((["--help"], 0), (["--unknown"], 2)):
        with pytest.raises(SystemExit) as stopped:
            command.run(argv)
        assert stopped.value.code == expected
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("errors", [[OSError], (BaseException,), (KeyboardInterrupt,), ("OSError",)])
def test_error_policy_is_fixed_exception_classes(errors):
    with pytest.raises(TypeError, match="Exception classes"):
        Group("example", "Example", errors=errors)
