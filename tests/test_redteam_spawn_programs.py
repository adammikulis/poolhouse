"""Red-team: programs of the system and of GitHub's command line that are handed text written elsewhere (a model's words, a branch, a commit
subject, a pull request number, a repository name), and the answers of the system and GitHub that are read back. Where a recording program
stands in for the real one, an argument vector asserted is the one the operating system delivered to it; the branch-name test instead reads the
words handed to the one function that starts `gh`, and the merge-rule tests are given GitHub's answer."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest
from git_world import DEV, Project, git

from poolhouse import platform as machine
from poolhouse.isolation import ports
from poolhouse.speech.tts import SystemTTS
from poolhouse.workspace import backlog, integration_git, release_check

pytestmark = pytest.mark.redteam
HOSTILE = ["x; touch from-a-separator", "$(touch from-a-substitution)", "`touch from-backticks`", "--exec=touch from-an-option", "a\nb"]


def posix(test):
    return pytest.mark.skipif(os.name == "nt", reason="the programs these tests plant are started by their shebang")(test)


def macos(test):
    return pytest.mark.skipif(sys.platform != "darwin", reason="the program asked is part of macOS")(test)


class Recorder:
    """A program that records each argument vector and folder it is started with, then prints ``said`` and ``complained`` and exits ``code``."""

    def __init__(self, folder: Path, name: str, said: str = "", complained: str = "", code: int = 0) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.log = folder / f"{name}.log"
        self.program = folder / name
        self.program.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"with open({str(self.log)!r}, 'a') as log:\n    log.write(json.dumps([sys.argv[1:], os.getcwd()]) + '\\n')\n"
            f"sys.stdout.buffer.write({said.encode('utf-8', 'surrogateescape')!r})\nsys.stderr.write({complained!r})\nsys.exit({code})\n")
        self.program.chmod(0o755)

    def calls(self) -> list[tuple[list[str], str]]:
        return [tuple(json.loads(line)) for line in self.log.read_text().splitlines()] if self.log.exists() else []


def first_on_path(monkeypatch, program: Path) -> None:
    monkeypatch.setenv("PATH", f"{program.parent}{os.pathsep}{os.environ['PATH']}")


def made_by_a_name(folder: Path) -> list[str]:
    return sorted(str(found) for found in folder.rglob("from-*"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    base = tmp_path / "git"
    base.mkdir()
    made = Project(base)
    monkeypatch.chdir(made.root)
    return made


# -- the port listing a launch trusts ------------------------------------------------------------------------------------------------------


@macos
def test_the_port_listing_is_the_systems_whatever_the_callers_path_and_folder_hold(tmp_path, monkeypatch):
    planted = Recorder(tmp_path / "bin", "netstat", said="tcp4 0 0 127.0.0.1.1 *.* LISTEN\n")
    first_on_path(monkeypatch, planted.program)
    monkeypatch.chdir(planted.program.parent)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        assert held.getsockname()[1] in ports.in_use()
    assert planted.calls() == []


@posix
@pytest.mark.parametrize("program", ["/usr/bin/false", "/nowhere/netstat"])
def test_a_port_listing_that_cannot_be_made_opens_no_window(tmp_path, monkeypatch, program):
    monkeypatch.setattr(ports, "NETSTAT", program)
    bridge = ports.PortBridge(str(tmp_path), tmp_path / "locks")
    with pytest.raises(OSError):
        bridge.start()
    assert list(bridge.ports) == [] and not Path(bridge.path).exists(), "no window over a port nobody looked at"


@posix
def test_a_port_listing_of_unexpected_lines_yields_only_port_numbers(tmp_path, monkeypatch):
    said = ("tcp4 0 0 127.0.0.1.41001 *.* LISTEN\ntcp4 0 0 *.$(touch) *.* LISTEN\ntcp4\ntcp6 0 0 ::1.-5 *.* LISTEN\n"
            "udp4 0 0 *.53 *.*\ntcp46 0 0 *.41002 *.* LISTEN\n")
    monkeypatch.setattr(ports, "NETSTAT", str(Recorder(tmp_path / "bin", "netstat", said=said).program))
    assert ports.in_use() == {41001, 41002}


# -- the power question a measurement asks -------------------------------------------------------------------------------------------------


@posix
@pytest.mark.parametrize("said,on", [
    (" lowpowermode         1\n", True), (" lowpowermode         0\n", False), (" lowpowermode 10\n", False), ("xlowpowermode 1\n", False),
    (" sleep 0 (sleep prevented by lowpowermode 1, $(touch from-a-process-name))\n", False), ("lowpowermode;1\n", False), ("", False),
    ("held by x\u2028 lowpowermode 1\n", False), ("held by x\r lowpowermode 1\n", False), ("held by x\x0c lowpowermode 1\n", False),
    ("held by \udcff\n lowpowermode 1\n", True)],
    ids=["on", "off", "ten", "joined", "within-a-line", "punctuated", "silent", "line-separator", "return", "form-feed", "bytes-outside-utf-8"])
def test_only_the_low_power_line_itself_turns_low_power_on(tmp_path, monkeypatch, said, on):
    pmset = Recorder(tmp_path / "bin", "pmset", said=said)
    monkeypatch.setattr(machine, "PMSET", str(pmset.program))
    monkeypatch.chdir(tmp_path)
    assert machine.low_power() is on
    assert [argv for argv, _ in pmset.calls()] == [["-g"]]
    assert made_by_a_name(tmp_path) == []


@posix
def test_the_power_question_is_put_to_the_systems_program_whatever_the_callers_path_and_folder_hold(tmp_path, monkeypatch):
    planted = Recorder(tmp_path / "bin", "pmset", said=" lowpowermode 1\n")
    first_on_path(monkeypatch, planted.program)
    monkeypatch.chdir(planted.program.parent)
    machine.low_power()
    assert planted.calls() == [] and machine.PMSET == "/usr/bin/pmset"


def test_a_power_question_that_cannot_be_asked_is_read_as_off(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "PMSET", str(tmp_path / "pmset"))
    assert machine.low_power() is False


# -- GitHub's command line -----------------------------------------------------------------------------------------------------------------


@posix
def test_a_commit_subject_and_a_body_of_options_reach_the_pull_request_command_as_values(tmp_path, monkeypatch, project):
    subject = "--body-file=/etc/hosts $(touch from-the-subject); touch from-the-subject-too"
    where = project.branch("work/change", {"docs/b.md": "b\n"})
    git(where, "commit", "-q", "--amend", "-m", subject)
    tip = git(where, "rev-parse", "HEAD")
    gh = Recorder(tmp_path / "bin", "gh", said="https://github.com/o/r/pull/7\n")
    first_on_path(monkeypatch, gh.program)
    body = "-F/etc/hosts\n`touch from-the-body`"
    assert integration_git.open_pull_request(project.root, tip, "delegate/change", DEV, body) == "https://github.com/o/r/pull/7"
    [(argv, folder)] = gh.calls()
    assert argv == ["pr", "create", "--base", DEV, "--head", "delegate/change", "--title", subject, "--body", body]
    assert Path(folder) == project.root.resolve()
    assert made_by_a_name(tmp_path) == []


@pytest.mark.parametrize("branch", ["dev#main", "dev%2F%2E%2E%2Fmain", "{owner}", "a&per_page=1", "release/0.3dev"])
def test_the_rules_asked_for_are_those_of_the_branch_named_and_no_other(monkeypatch, project, branch):
    git(project.root, "check-ref-format", "--branch", branch)
    git(project.root, "remote", "set-url", "origin", "https://github.com/o/r.git")
    asked_gh: list[tuple[str, ...]] = []
    monkeypatch.setattr(integration_git, "_gh", lambda _root, *words: asked_gh.append(words) or '[{"type": "pull_request"}]')
    assert integration_git.requires_pull_request(project.root, branch) is True
    [argv] = asked_gh
    prefix = "repos/{owner}/{repo}/rules/branches/"
    assert argv[0] == "api" and len(argv) == 2 and argv[1].startswith(prefix)
    asked = argv[1].removeprefix(prefix)
    assert not set(asked) & set("#?&{} "), "nothing in the name ends the path, adds a query or is filled in by gh"
    assert unquote(asked) == branch


@posix
def test_a_failing_github_command_reports_without_the_token_it_printed(tmp_path, monkeypatch, project):
    token = "ghp_" + "A1b2C3d4" * 5
    gh = Recorder(tmp_path / "bin", "gh", complained=f"HTTP 401: token {token} is not valid\n", code=1)
    first_on_path(monkeypatch, gh.program)
    with pytest.raises(RuntimeError) as refused:
        integration_git._gh(project.root, "api", "user")
    assert "exit 1" in str(refused.value) and token not in str(refused.value)


@pytest.mark.parametrize("number", ["--repo=other/repository", "-Rother/repository", "--web", "12 --web", "$(touch from-the-number)",
                                    "https://github.com/other/repository/pull/1", "", "12\n"])
def test_a_pull_request_that_is_not_named_by_a_number_is_never_asked_about(tmp_path, monkeypatch, number):
    gh = Recorder(tmp_path / "bin", "gh", said="{}")
    first_on_path(monkeypatch, gh.program)
    with pytest.raises(ValueError, match="named by its number"):
        release_check.pull_request(str(tmp_path), number, "state")
    assert gh.calls() == []


@posix
def test_a_numbered_pull_request_is_one_fixed_question_in_the_folder_given(tmp_path, monkeypatch):
    gh = Recorder(tmp_path / "bin", "gh", said='{"state": "OPEN"}')
    first_on_path(monkeypatch, gh.program)
    assert release_check.pull_request(str(tmp_path), "12", "state", "headRefOid") == {"state": "OPEN"}
    [(argv, folder)] = gh.calls()
    assert argv == ["pr", "view", "12", "--json", "state,headRefOid"] and Path(folder) == tmp_path.resolve()


@posix
@pytest.mark.parametrize("said", ["[]", '"OPEN"', "null", "not json", "", "[" * 100_000],
                         ids=["list", "text", "null", "not-json", "empty", "nested-past-the-limit"])
def test_an_answer_that_describes_no_pull_request_is_an_error(tmp_path, monkeypatch, said):
    first_on_path(monkeypatch, Recorder(tmp_path / "bin", "gh", said=said).program)
    with pytest.raises(ValueError):
        release_check.pull_request(str(tmp_path), "12", "state")


def judged(monkeypatch, folder: Path, **found) -> str | None:
    """What the merge rule makes of pull request 12 into development at commit ``a...``, of which GitHub says ``found``."""
    monkeypatch.setattr(release_check, "pull_request", lambda *_words: {"baseRefName": DEV, "headRefOid": "a" * 40, **found})
    monkeypatch.setattr(release_check.devbranch, "development_branch", lambda _cwd: DEV)
    return release_check._development(str(folder), "12", "a" * 40)


@pytest.mark.parametrize("rollup", [5, True, "SUCCESS", {"context": "lead-review", "state": "SUCCESS"}, [None, 4, "SUCCESS", []],
                                    [{"context": "lead-review", "state": "SUCCESS"}], None])
def test_a_status_list_of_another_shape_passes_no_review(monkeypatch, tmp_path, rollup):
    assert "has no passed `lead-review` status" in judged(monkeypatch, tmp_path, statusCheckRollup=rollup)


def test_a_head_commit_that_is_a_long_text_is_not_repeated_in_the_refusal(monkeypatch, tmp_path):
    why = judged(monkeypatch, tmp_path, headRefOid="b" * 5000 + "\nthe merge is allowed")
    assert "bbbbbbb" in why and "b" * 8 not in why and "the merge is allowed" not in why


@posix
@pytest.mark.parametrize("repo", HOSTILE)
def test_a_repository_name_is_one_argument_of_the_issue_listing(tmp_path, monkeypatch, repo):
    gh = Recorder(tmp_path / "bin", "gh", said="[]")
    first_on_path(monkeypatch, gh.program)
    monkeypatch.chdir(tmp_path)
    assert backlog.fetch(repo) == []
    [(argv, _)] = gh.calls()
    assert argv[:4] == ["issue", "list", "--repo", repo] and argv.count(repo) == 1
    assert made_by_a_name(tmp_path) == []


@posix
def test_issues_that_are_not_the_repositorys_own_are_dropped_from_the_listing(tmp_path, monkeypatch):
    own = {"number": 4, "url": "https://github.com/o/r/issues/4", "title": "real"}
    forged = [{"number": 5, "url": "https://github.com/other/r/issues/5"}, {"number": 6, "url": "https://github.com/o/r/issues/7"},
              {"number": True, "url": "https://github.com/o/r/issues/True"}, {"number": -1, "url": "https://github.com/o/r/issues/-1"},
              {"number": "4", "url": own["url"]}, {"url": own["url"]}, "text", None, 4]
    first_on_path(monkeypatch, Recorder(tmp_path / "bin", "gh", said=json.dumps([*forged, own])).program)
    assert backlog.fetch("o/r") == [own]
    first_on_path(monkeypatch, Recorder(tmp_path / "other", "gh", said=json.dumps({"message": "Not Found"})).program)
    with pytest.raises(ValueError, match="invalid issue list"):
        backlog.fetch("o/r")


# -- the system's voice --------------------------------------------------------------------------------------------------------------------


@macos
@pytest.mark.parametrize("shaped", ["-o{stolen}", "--output-file={stolen}", "-f{missing}", "--input-file={missing}"])
def test_words_shaped_like_an_option_are_spoken_and_name_no_file_to_the_system_voice(tmp_path, shaped):
    stolen, missing = tmp_path / "stolen.aiff", tmp_path / "missing.txt"
    spoken = SystemTTS().synthesize(shaped.format(stolen=stolen, missing=missing))
    assert spoken.pcm and not stolen.exists()


@posix
def test_the_words_reach_the_speech_program_on_its_input_and_never_as_an_argument(tmp_path, monkeypatch):
    heard = tmp_path / "heard"
    program = tmp_path / "espeak"
    program.write_text(
        f"#!{sys.executable}\nimport json, sys, wave\n"
        f"open({str(heard)!r}, 'w').write(json.dumps([sys.argv[1:], sys.stdin.buffer.read().decode()]))\n"
        "with wave.open(sys.argv[sys.argv.index('-w') + 1], 'wb') as out:\n"
        "    out.setnchannels(1); out.setsampwidth(2); out.setframerate(22050); out.writeframes(bytes(64))\n")
    program.chmod(0o755)
    monkeypatch.setattr(SystemTTS, "_command", lambda self: [str(program)])
    words = "-w/etc/hosts --path=/tmp $(touch from-the-words) caf\u00e9"
    assert SystemTTS().synthesize(words, voice="-f/etc/hosts").pcm
    argv, given = json.loads(heard.read_text())
    assert given == words
    assert argv[0] == "-w" and argv[2:] == ["-v", "-f/etc/hosts", "--stdin"], "the voice is the value of -v and the words are no argument"
