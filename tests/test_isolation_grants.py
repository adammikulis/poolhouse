"""Grants: the five kinds of widening, decided at once under the caps, each cap attacked.

Every test runs the real checks on real files: a project on disk with hostile names, a home folder with credential folders, a state root, and
real devices and inodes. Nothing here mocks the file system or the sandbox.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse.contracts.loader import ContractError
from poolhouse.isolation import Decision, Request, Rule, decide, egress, publish, toolchain
from poolhouse.isolation.egress import Host
from poolhouse.isolation.policy import ProjectPolicy
from poolhouse.isolation.refusals import refusal_line
from tests.isolation_kit import REGISTRY, hostile_project, person_home

ISSUER = "user test pid 1"


def ask(kind: str, subject: str, rule: Rule, source: str = "task") -> Decision:
    return decide(Request(kind, subject, ISSUER, source, rule.project), rule)


@pytest.fixture
def rule(tmp_path, monkeypatch):
    person_home(tmp_path, monkeypatch)
    return Rule(hostile_project(tmp_path), registry=REGISTRY, scope="session-1")


def test_a_decision_is_granted_or_refused_and_nothing_else(rule):
    asked = [("read", str(Path(rule.project).parent)), ("net", "pypi.org"), ("net", "evil.example"), ("publish", "src/app.py"),
             ("publish", "Makefile"), ("in-place", rule.project), ("toolchain", "/nonexistent"), ("unknown", "x")]
    for kind, subject in asked:
        decision = ask(kind, subject, rule)
        assert type(decision) is Decision and type(decision.granted) is bool
        assert (decision.grant is not None) is decision.granted
        assert decision.line == refusal_line(kind) if kind != "unknown" and not decision.granted else True
    assert ask("net", "pypi.org", rule).granted and ask("publish", "src/app.py", rule).granted


def test_a_refusal_has_one_form_that_names_the_kind_and_never_the_subject_the_session_chose(rule):
    for kind, subject in (("net", "evil.example"), ("publish", "Makefile"), ("read", "/etc"), ("toolchain", "/usr/share")):
        refused = ask(kind, subject, rule)
        assert not refused.granted and subject not in refused.line and refused.line == refusal_line(kind)
        assert refused.cause and refused.cause not in refused.line


NET_REFUSED = ["evil.example", "10.0.0.1", "127.0.0.1", "*.pypi.org", "pypi.org:443", "PYPI.ORG", "pypi.org/simple", "pypi.org.", "",
               "files.pythonhosted.org.evil.example", "xn--pypi-9ra.org"]


@pytest.mark.parametrize("host", NET_REFUSED)
def test_a_host_that_the_registry_does_not_name_is_refused_whatever_its_spelling(rule, host):
    assert not ask("net", host, rule).granted
    assert not ask("net", host, replace(rule, policy=ProjectPolicy(net=(host,))), "policy").granted


def test_in_place_with_any_network_host_in_one_launch_refuses_both(rule):
    launch = (Request("in-place", rule.project, ISSUER), Request("net", "pypi.org", ISSUER))
    together = replace(rule, launch=launch)
    assert not ask("in-place", rule.project, together).granted and not ask("net", "pypi.org", together).granted
    assert ask("in-place", rule.project, replace(rule, launch=launch[:1])).granted
    assert ask("net", "pypi.org", replace(rule, launch=launch[1:])).granted


@pytest.mark.parametrize("subject", ["", ".", "./", "/", "src", "src/", "docs", "*", "src/*.py", "**/*.py", "src/app.?y", "{a,b}.py", "[a].py",
                                     ".github/CODEOWNERS", "src/.hidden.py", ".env", ".claude/settings.json", "../outside.py", "src/../Makefile",
                                     "Makefile", "package.json", "pyproject.toml", "CLAUDE.md", "AGENTS.md", "docs/CODEOWNERS", "CODEOWNERS",
                                     "gradlew", "lib/tool.jar", "scripts/build.py", "lefthook.yml", "Dockerfile", "yarn.lock",
                                     "link-out.py", "linked-dir/x.py", "src/.", "id_rsa", "server.pem", "special.fifo", "a\nb.py"])
def test_publish_takes_exact_files_and_refuses_the_root_a_directory_a_glob_a_dot_path_and_every_governing_file(rule, subject):
    assert not ask("publish", subject, rule).granted, subject
    assert not ask("publish", subject, replace(rule, policy=ProjectPolicy(publish=(subject,))), "policy").granted, subject


def test_publish_grants_an_exact_plain_file_present_or_new_and_nothing_beside_it(rule):
    for subject in ("src/app.py", "docs/Notes.md", "src/new_module.py", str(Path(rule.project) / "src" / "app.py")):
        decision = ask("publish", subject, rule)
        assert decision.granted, (subject, decision.cause)
        shown = Path(subject).relative_to(rule.project).as_posix() if Path(subject).is_absolute() else subject
        assert decision.grant.subject == shown


def test_a_name_that_folds_with_another_is_refused_and_a_link_is_never_a_rule(rule):
    project = Path(rule.project)
    (project / "src" / "Twin.py").write_text("x = 1\n")
    (project / "src" / "twin.py").write_text("x = 2\n")
    names = [each.name for each in (project / "src").iterdir()]
    folds = len(names) == len({name.casefold() for name in names})
    assert folds or not ask("publish", "src/Twin.py", rule).granted, "two names that fold to one are refused"
    (project / "src" / "é.py").write_text("x = 3\n")
    (project / "src" / "é.py").write_text("x = 4\n")
    exists = {each.name for each in (project / "src").iterdir() if each.name.endswith(".py") and not each.name.isascii()}
    assert len(exists) < 2 or not (ask("publish", "src/é.py", rule).granted or ask("publish", "src/é.py", rule).granted)
    assert not ask("publish", "link-out.py", rule).granted and not ask("publish", "linked-dir/x.py", rule).granted


def test_a_publish_rule_is_bound_to_the_files_device_and_inode_and_not_to_a_string(rule):
    project = Path(rule.project)
    bound = ask("publish", "docs/Notes.md", rule).grant.held
    assert publish.matches(bound, project, "docs/Notes.md")
    assert not publish.matches(bound, project, "DOCS/Notes.md") and not publish.matches(bound, project, "docs/notes.md")
    (project / "docs" / "Notes.md").unlink()
    (project / "docs" / "Notes.md").write_text("replaced\n")
    assert not publish.matches(bound, project, "docs/Notes.md"), "a replaced file is another object"
    again = ask("publish", "docs/Notes.md", rule).grant.held
    assert publish.matches(again, project, "docs/Notes.md")
    (project / "docs").rename(project / "docs-away")
    (project / "docs").mkdir()
    shutil.copy2(project / "docs-away" / "Notes.md", project / "docs" / "Notes.md")
    assert not publish.matches(again, project, "docs/Notes.md"), "a directory the person swapped matches nothing"
    new = ask("publish", "docs/Fresh.md", rule).grant.held
    assert new.file is None and publish.matches(new, project, "docs/Fresh.md")
    (project / "docs" / "Fresh.md").write_text("appeared\n")
    assert not publish.matches(new, project, "docs/Fresh.md"), "a file that was not there is not one that appeared"


def test_a_folder_that_holds_a_credential_or_is_above_the_home_or_the_state_root_is_never_a_read(rule, tmp_path):
    house = Path(os.environ["HOME"])
    for folder in (house / ".ssh", house / ".aws", house, house.parent, Path("/"), Path(os.environ["POOLHOUSE_HOME"]),
                   Path(os.environ["POOLHOUSE_HOME"]) / "node", Path(rule.project) / "holds-a-key"):
        assert not ask("read", str(folder), rule).granted, folder
    other = tmp_path / "toolsrc"
    other.mkdir()
    (other / "lib.py").write_text("x = 1\n")
    granted = ask("read", str(other), rule)
    assert granted.granted and granted.grant.subject == os.path.realpath(other)
    assert "(it cannot change it)" in granted.line


def test_a_toolchain_folder_must_hold_a_program_and_is_granted_by_the_object_that_was_checked(rule, tmp_path):
    version = tmp_path / "versions" / "3.99"
    (version / "bin").mkdir(parents=True)
    program = version / "bin" / "python"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    empty = tmp_path / "versions" / "empty"
    empty.mkdir()
    assert not ask("toolchain", str(empty), rule).granted and not ask("toolchain", "/usr/share", rule).granted
    farm = tmp_path / "farm"
    farm.symlink_to(version)
    granted = ask("toolchain", str(farm), rule)
    assert granted.granted and granted.grant.subject == os.path.realpath(version) and isinstance(granted.grant.held, toolchain.Resolved)
    secret = Path(os.environ["HOME"]) / ".ssh"
    farm.unlink()
    farm.symlink_to(secret)
    assert granted.grant.held.path == os.path.realpath(version) and granted.grant.held.unchanged(), "a later swap changes nothing granted"
    assert not ask("toolchain", str(farm), rule).granted, "and the swapped link is a credential folder"
    version.rename(tmp_path / "versions" / "moved")
    version.mkdir()
    assert not granted.grant.held.unchanged(), "a directory replaced under the same name is a deviation after the proof"
    granted.grant.held.close()


def test_a_toolchain_declaration_is_resolved_once_and_granted_by_the_same_object(rule, tmp_path):
    version = Path(os.environ["HOME"]) / ".pyenv" / "versions" / "3.99"
    (version / "bin").mkdir(parents=True)
    (version / "bin" / "python3").write_text("#!/bin/sh\nexit 0\n")
    (version / "bin" / "python3").chmod(0o755)
    project = Path(rule.project)
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text(f"home = {version / 'bin'}\nversion = 3.99\n")
    assert toolchain.declared(project) == [os.path.realpath(version)]
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text(f"home = {Path(os.environ['HOME']) / '.ssh'}\n")
    assert toolchain.declared(project) == [], "a pyvenv.cfg that names the credential folder names nothing"
    (project / ".venv" / "pyvenv.cfg").write_text(f"home = {version / 'bin'}\n")
    declared = Request("toolchain", os.path.realpath(version), ISSUER, "declaration", str(project))
    assert decide(declared, rule).granted
    assert not decide(replace(declared, subject="/usr/share"), rule).granted, "a folder the project does not declare is not a declaration"
    assert not decide(replace(declared, kind="read"), rule).granted, "only a toolchain may claim a declaration"
    assert toolchain.resolve(tmp_path / "farm-of-links" / "x") is None
    link_part = tmp_path / "via"
    link_part.symlink_to(version)
    assert toolchain.resolve(link_part / "bin").path == os.path.realpath(version / "bin")
    held = toolchain._open(os.path.realpath(version))
    os.close(held)
    with pytest.raises(OSError):
        toolchain._open(str(link_part))


def test_a_widening_argument_outside_a_present_project_policy_is_refused_and_a_policy_in_the_repository_is_not_read(rule, tmp_path):
    from poolhouse.isolation import policy as policies
    project = Path(rule.project)
    assert ask("net", "pypi.org", rule).granted, "no policy is no restriction beyond the caps"
    assert not ask("net", "pypi.org", rule, "policy").granted, "a claimed policy that does not exist names nothing"
    for hostile in (project / ".poolhouse-project.json", project / "policy.json", project / ".poolhouse-policy.json"):
        hostile.write_text('{"net": ["pypi.org"], "publish": ["src/app.py"], "in-place": true, "read": ["/"]}')
    assert not ask("net", "pypi.org", rule, "policy").granted and not ask("publish", "src/app.py", rule, "policy").granted
    for not_a_source in ("board", "patch", "file", "refusal", "tool-result", "agent", ""):
        assert not ask("net", "pypi.org", rule, not_a_source).granted, not_a_source
    assert policies.load(project) is None
    policies.save(project, ProjectPolicy(net=("pypi.org",), publish=("docs/Notes.md",)), {})
    bounded = replace(rule, policy=policies.load(project))
    assert ask("net", "pypi.org", bounded).granted and ask("net", "pypi.org", bounded, "policy").granted
    assert ask("publish", "docs/Notes.md", bounded, "policy").granted
    assert not ask("publish", "src/app.py", bounded).granted, "a present policy bounds a task that names more"
    assert not ask("in-place", rule.project, bounded).granted and not ask("read", str(tmp_path), bounded).granted
    path = policies.path_of(project)
    path.write_text("not json")
    assert policies.load(project) == ProjectPolicy(), "a damaged policy names nothing, so it narrows"
    assert not ask("net", "pypi.org", replace(rule, policy=policies.load(project))).granted


def test_a_policy_is_written_only_by_a_process_that_is_neither_an_agent_nor_inside_a_confinement(rule, monkeypatch):
    from poolhouse.caller import AgentRefused
    from poolhouse.isolation import policy as policies
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(AgentRefused):
        policies.save(rule.project, ProjectPolicy(net=("pypi.org",)))
    assert policies.load(rule.project) is None


def entry(**changes):
    base = {"host": "pypi.org", "ecosystem": "python", "source": "https://pypi.org/help/", "bytes_out_bound": 5000,
            "measured": {"bytes_out": 4000, "command": "pip download", "date": "2026-11-24"}}
    return {**base, **changes}


@pytest.mark.parametrize("damage", [{"source": ""}, {"source": "http://pypi.org/"}, {"measured": {}}, {"measured": {"bytes_out": 1}},
                                    {"bytes_out_bound": 3999}, {"bytes_out_bound": True}, {"host": "10.0.0.1"}, {"host": "*.org"}, {"ecosystem": ""}])
def test_an_entry_of_the_host_list_without_its_source_or_its_measured_bound_is_not_a_host(damage):
    with pytest.raises(ContractError):
        egress._entry(entry(**damage))
    assert egress._entry(entry()) == Host("pypi.org", "python", "https://pypi.org/help/", 5000, "pip download", "2026-11-24")
