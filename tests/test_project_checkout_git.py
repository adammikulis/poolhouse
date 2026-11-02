"""Source checkout ignores inherited Git configuration and templates."""

import pytest

from poolhouse.fleet import project_source
from poolhouse.net import git

IDENTIFIER = "a" * 32


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git.run(["init"], cwd=root)
    (root / "run.sh").write_text("#!/bin/sh\necho hello\n")
    (root / ".gitattributes").write_text("*.sh filter=malicious\n")
    git.run(["add", "--", "run.sh", ".gitattributes"], cwd=root)
    git.run(["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-m", "source"], cwd=root)
    manifest, packed = project_source.build(root, IDENTIFIER)
    return manifest, packed


def test_checkout_does_not_execute_ambient_clean_filter(bundle, tmp_path, monkeypatch):
    manifest, packed = bundle
    config = tmp_path / "gitconfig"
    config.write_text('[filter "malicious"]\n\tsmudge = false\n\trequired = true\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    target = project_source.checkout(packed, IDENTIFIER, manifest["source_hash"], tmp_path / "checkout")
    assert (target / "run.sh").read_text() == "#!/bin/sh\necho hello\n"
    assert (target / ".gitattributes").is_file()


def test_checkout_ignores_ambient_template_and_git_selectors(bundle, tmp_path, monkeypatch):
    manifest, packed = bundle
    template = tmp_path / "private-template"
    template.mkdir()
    (template / "private-marker").write_text("synthetic private data")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git.run(["init"], cwd=foreign)
    config = tmp_path / "gitconfig"
    config.write_text(f'[init]\n\ttemplateDir = {template}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "init.templateDir")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(template))
    target = project_source.checkout(packed, IDENTIFIER, manifest["source_hash"], tmp_path / "checkout")
    assert not (target / ".git" / "private-marker").exists()
    assert (target / "run.sh").read_text() == "#!/bin/sh\necho hello\n"
    assert not (foreign / "run.sh").exists()
