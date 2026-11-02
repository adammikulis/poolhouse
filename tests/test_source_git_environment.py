"""The isolated source Git environment retains required Windows system paths."""

from poolhouse.activity import source_snapshot
from poolhouse.net import git


def test_windows_source_git_environment_uses_os_directory_without_inherited_configuration(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", "C:\\untrusted")
    monkeypatch.setenv("GIT_DIR", "C:\\foreign")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    with monkeypatch.context() as scoped:
        scoped.setattr(source_snapshot.sys, "platform", "win32")
        scoped.setattr(source_snapshot, "_windows_directory", lambda: "C:\\Windows", raising=False)
        env = source_snapshot.git_environment()
    assert env["SYSTEMROOT"] == env["WINDIR"] == "C:\\Windows"
    assert env["PATH"] == "C:\\Windows\\System32;C:\\Windows"
    assert not {"GIT_DIR", "GIT_CONFIG_COUNT", "OPENAI_API_KEY"} & env.keys()


def test_windows_source_checkout_uses_its_private_temporary_directory(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    environments = []
    monkeypatch.setattr(git, "_execute", lambda _args, _cwd, env: environments.append(env))
    with monkeypatch.context() as scoped:
        scoped.setattr(source_snapshot.sys, "platform", "win32")
        scoped.setattr(source_snapshot, "_windows_directory", lambda: "C:\\Windows", raising=False)
        git.source_init(stage)
    (env,) = environments
    assert env["TEMP"] == env["TMP"] == str(tmp_path)
