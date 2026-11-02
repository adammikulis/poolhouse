"""An agent runs `release-key create --write` and `github-protection-apply --apply` only through the owner's one-time approval:
the question, the hook that records the answer, the Bash guard's ticket, and the scripts that spend it, against real hook processes."""

from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import json
import os
import pwd
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
from person_support import (
    HOOKS,
    ROOT,
    SESSION,
    answer_event,
    environment,
    git,
    rows,
    run_hook as hook_process,
    say,
)

from poolhouse import credentials, protectedwrites
from poolhouse.workspace import person_auth, person_store, run_approval, run_hook as writer
from tests import memory_keys
from tests.github_support import GOOD, REPO, ruleset, serve

ring = memory_keys.ring
SLUG = REPO
ORIGIN = f"https://github.com/{SLUG}.git"
KEY = ["release-key", "create", "--write"]
APPLY = ["github-protection-apply", "--apply", "--repo", SLUG]
AGENT = {"CLAUDECODE": "1"}
NO_TERMINAL = (False, False)
OTHER = "sess-someone-else"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A checkout with both scripts, a GitHub origin and a person state; ``(tmp, state, repo)``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for name in ("release-key", "github-protection-apply"):
        script = repo / "scripts" / name
        script.parent.mkdir(exist_ok=True)
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src" / "code.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("note\n")
    git(repo, "add", "scripts", "src", "docs")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "remote", "add", "origin", ORIGIN)
    for name in ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", run_approval.TICKET_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    return tmp_path, tmp_path / "state", repo


def propose(repo, state, *words):
    return subprocess.run([sys.executable, str(HOOKS / "run-approval"), "propose", *words], cwd=repo, text=True,
                          capture_output=True, timeout=60, check=False, env=environment(state))


def asked(done):
    """The question, the approving answer and the declining answer a `propose` printed."""
    assert done.returncode == 0, done.stderr
    text, _, options = done.stdout.strip().partition("\n\noptions: ")
    yes, _, no = options.partition(" | ")
    return text, yes, no


def approve(tmp, state, repo, *words, **event):
    """Bind the session, ask the question for ``words`` and answer it with the approval; the hook's process."""
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *words))
    return hook_process(answer_event(repo, question, yes, **event), state)


def records(state, kind):
    return [r for r in rows(state) if r["type"] == "authorization" and r["kind"] == kind]


def spent(state):
    return [r for r in rows(state) if r["type"] == "transition" and r["state"] == "used"]


def guard(command, state, cwd, env=None, **event):
    env = environment(state, POOLHOUSE_HOOK_STATE=str(state.parent / "hook-state"), **(env or {}))
    return subprocess.run([sys.executable, str(HOOKS / "claude-bash-guard")], text=True, capture_output=True, timeout=60,
                          check=False, env=env, input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                                                                  "cwd": str(cwd), **event}))


def ticket_of(done):
    """The ticket the guard put in the command's shell, an empty string when it put none."""
    if not done.stdout.strip():
        return ""
    lines = json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"]["command"].splitlines()
    held = [line.split("=", 1)[1] for line in lines if line.startswith(f"export {run_approval.TICKET_ENV}=")]
    return held[0] if held else ""


def ticket_for(tmp, state, repo, *words):
    """Approve ``words`` and run its exact command through the guard; the ticket."""
    assert approve(tmp, state, repo, *words).returncode == 0
    return ticket_of(guard(" ".join(f"scripts/{words[0]}" if i == 0 else w for i, w in enumerate(words)), state, repo))


# -- the question ---------------------------------------------------------------------------


@pytest.mark.parametrize("words", [KEY, APPLY, [*APPLY, "--strict-tags", "--owner-review"]])
def test_the_question_is_made_of_the_command_the_repository_the_commit_and_the_registry(world, words):
    _, state, repo = world
    question, yes, no = asked(propose(repo, state, *words))
    sha = git(repo, "rev-parse", "HEAD")
    assert question.startswith(f"Run scripts/{words[0]} ")
    assert f"Repository: {SLUG}" in question and f"Commit: {sha}" in question and f"Checkout: {repo.resolve()}" in question
    assert yes.startswith("Approve scripts/") and no
    assert question.splitlines()[-1].startswith("authorize run-script ") and " 15m x1 #" in question.splitlines()[-1]


def test_the_options_of_a_covered_command_are_the_same_whatever_order_its_flags_come_in(world):
    _, state, repo = world
    one = asked(propose(repo, state, *APPLY, "--strict-tags", "--owner-review"))
    two = asked(propose(repo, state, "github-protection-apply", "--owner-review", "--repo", SLUG, "--strict-tags", "--apply"))
    assert one == two


@pytest.mark.parametrize("words", [
    ["release-key", "create"], ["release-key", "rotate", "--write"], ["release-key", "show-public"], ["release-key", "agent"],
    [*KEY, "extra"], ["github-protection-apply", "--apply"], ["github-protection-apply", "--repo", SLUG],
    ["github-protection-apply", "--apply", "--repo", "someone/else"], [*APPLY, "--apply"], [*APPLY, "--restore", "backup.json"],
    [*APPLY, "--repo", SLUG], ["agent-identity", "setup"], ["../../bin/ls"], ["release-key/../x"], []])
def test_only_the_two_covered_commands_can_be_put_to_the_owner(world, words):
    _, state, repo = world
    done = propose(repo, state, *words)
    assert done.returncode != 0 and "question" not in done.stdout and not (state / "person").exists()


def test_a_change_in_scripts_or_src_stops_the_question_but_a_change_elsewhere_does_not(world):
    _, state, repo = world
    (repo / "docs" / "note.md").write_text("changed\n")
    assert propose(repo, state, *KEY).returncode == 0
    (repo / "src" / "code.py").write_text("x = 2\n")
    assert propose(repo, state, *KEY).returncode != 0
    git(repo, "checkout", "--", "src/code.py")
    (repo / "scripts" / "extra.py").write_text("import sys\n")
    assert propose(repo, state, *KEY).returncode != 0
    (repo / "scripts" / "extra.py").unlink()
    (repo / "scripts" / "release-key").write_text("#!/bin/sh\necho changed\n")
    assert propose(repo, state, *KEY).returncode != 0


@pytest.mark.parametrize("url", ["https://example.com/o/r.git", "https://github.com.evil.example/o/r.git",
                                 "git@github.com:o/r/extra.git", "/srv/git/r.git", "https://github.com/o"])
def test_an_origin_that_is_not_a_github_repository_is_not_a_repository_to_approve(world, url):
    _, state, repo = world
    git(repo, "remote", "set-url", "origin", url)
    assert propose(repo, state, *KEY).returncode != 0


@pytest.mark.parametrize("url", [f"git@github.com:{SLUG}.git", f"ssh://git@github.com/{SLUG}.git", f"https://github.com/{SLUG}"])
def test_the_usual_forms_of_a_github_origin_name_the_same_repository(world, url):
    _, state, repo = world
    git(repo, "remote", "set-url", "origin", url)
    assert f"Repository: {SLUG}" in asked(propose(repo, state, *KEY))[0]


# -- the answer ------------------------------------------------------------------------------


def test_the_approving_answer_records_one_authorization_bound_to_the_exact_run(world):
    tmp, state, repo = world
    done = approve(tmp, state, repo, *KEY)
    assert done.returncode == 0 and "attested by hook" in done.stdout
    (auth,) = records(state, "run-script")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    assert auth["target"] == f"{repo.resolve()}|release-key|create,--write|{SLUG}|{tree}"
    assert auth["uses"] == 1 and auth["session_id"] == SESSION and auth["how"] == "asked"
    assert 0 < auth["expires"] - auth["ts"] <= 15 * 60 + 1


def test_the_same_answer_twice_leaves_one_live_authorization(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    question, yes, _ = asked(propose(repo, state, *KEY))
    hook_process(answer_event(repo, question, yes), state)
    assert len(records(state, "run-script")) == 1


def test_declining_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, _, no = asked(propose(repo, state, *KEY))
    hook_process(answer_event(repo, question, no), state)
    assert records(state, "run-script") == []


def test_an_answer_to_a_question_the_agent_wrote_itself_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *KEY))
    forged = {
        "no echo": "\n".join(question.splitlines()[:-1]),
        "a changed word": question.replace("Run scripts/release-key", "Run scripts/release-key now"),
        "another tag": question[:-12] + "0" * 12,
        "another command, same echo": question.replace("What it does:", "What it does: also reads the key. "),
        "no tag": question.rsplit(" #", 1)[0],
        "extra text after the echo": question + "\nApprove everything",
    }
    for label, text in forged.items():
        hook_process(answer_event(repo, text, yes), state)
        assert records(state, "run-script") == [], label


def test_only_the_approving_label_of_this_exact_question_records_it(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *KEY))
    for label in ("Approve release of abcdef0", "Approve scripts/release-key create", yes.upper(), yes + " ", "yes", "Do not run it"):
        hook_process(answer_event(repo, question, label), state)
    assert records(state, "run-script") == []


def test_an_answer_after_the_checkout_moved_on_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *KEY))
    (repo / "src" / "code.py").write_text("x = 2\n")
    git(repo, "add", "src/code.py")
    git(repo, "commit", "-q", "-m", "change the code after the question")
    hook_process(answer_event(repo, question, yes), state)
    assert records(state, "run-script") == []


def test_a_question_asked_in_one_repository_does_not_approve_a_run_in_another(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *KEY))
    other = tmp / "other"
    subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True)
    hook_process(answer_event(other, question, yes), state)
    assert records(state, "run-script") == []


def test_a_subagent_or_an_unattended_session_cannot_answer(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *KEY))
    hook_process(answer_event(repo, question, yes, agent_id="helper-1"), state)
    unattended = subprocess.run([sys.executable, str(HOOKS / "claude-user-prompt")], text=True, capture_output=True,
                                input=json.dumps(answer_event(repo, question, yes)), check=False,
                                env=environment(state, POOLHOUSE_NONINTERACTIVE="1"))
    assert unattended.returncode == 0 and records(state, "run-script") == []


@pytest.mark.parametrize("words", ["yes, run the release key", "approved: scripts/release-key create --write", "/allow run-script",
                                   "go ahead and apply the protection", "ok"])
def test_typed_words_never_approve_a_run(world, words):
    tmp, state, repo = world
    say(tmp, state, repo, words, prompt_id="typed")
    assert records(state, "run-script") == []


def test_stopping_ends_the_approval(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    say(tmp, state, repo, "stop", prompt_id="stop")
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


# -- the Bash guard's ticket -----------------------------------------------------------------


def test_the_main_sessions_exact_command_gets_one_ticket_and_spends_the_approval(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    done = guard("scripts/release-key create --write", state, repo)
    secret = ticket_of(done)
    assert done.returncode == 0 and len(secret) == 32
    assert len(spent(state)) == 1
    (ticket,) = records(state, "run-ticket")
    assert ticket["target"].endswith(f"|{SLUG}|{git(repo, 'rev-parse', 'HEAD^{tree}')}#{secret}")
    assert 0 < ticket["expires"] - ticket["ts"] <= 61
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert len(spent(state)) == 1


def test_the_ticket_is_in_the_shell_and_not_in_the_command_the_agent_wrote(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    command = json.loads(guard("scripts/release-key create --write", state, repo).stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    assert command.splitlines()[-1] == "scripts/release-key create --write"


@pytest.mark.parametrize("command", ["scripts/release-key create --write", "./scripts/release-key create --write",
                                     "{repo}/scripts/release-key create --write", "scripts/release-key  create   --write"])
def test_the_exact_approved_command_gets_a_ticket_however_its_path_is_written(world, command):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    assert len(ticket_of(guard(command.replace("{repo}", str(repo)), state, repo))) == 32


@pytest.mark.parametrize("command", [
    "cd scripts && ./release-key create --write", "scripts/release-key create --write && echo done", "scripts/release-key create --write; id",
    "scripts/release-key create --write | cat", "scripts/release-key create --write > /tmp/out",
    "scripts/release-key create --write $(id)", "scripts/release-key create --write `id`", "FOO=1 scripts/release-key create --write",
    "env scripts/release-key create --write", "sh -c 'scripts/release-key create --write'", "python3 scripts/release-key create --write",
    "scripts/release-key create --write extra", "scripts/release-key create", "scripts/release-key create --write --write",
    "scripts/release-key rotate --write", "scripts/github-protection-apply --apply --repo ownerlogin/poolhouse-test",
    "cp scripts/release-key /tmp/x && /tmp/x create --write", "scripts/release-key\ncreate --write",
    "scripts/release-key create --write\nscripts/release-key create --write", "scripts/release-key create --write # ok",
    "'scripts/release-key' create --write", "scripts/release-key create '--write'"])
def test_nothing_but_the_exact_approved_command_gets_a_ticket(world, command):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    assert ticket_of(guard(command, state, repo)) == "" and spent(state) == [], command


def test_a_command_run_from_a_folder_outside_the_checkout_or_in_another_checkout_gets_no_ticket(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    other = tmp / "other"
    subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True)
    assert ticket_of(guard("scripts/release-key create --write", state, tmp)) == ""
    assert ticket_of(guard("scripts/release-key create --write", state, other)) == ""
    assert ticket_of(guard(f"{other}/scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


def test_a_copy_or_a_link_named_like_the_script_is_not_the_script(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    evil = tmp / "evil" / "release-key"
    evil.parent.mkdir()
    evil.write_text("#!/bin/sh\n")
    evil.chmod(0o755)
    assert ticket_of(guard(f"{evil} create --write", state, repo)) == ""
    link = repo / "scripts" / "linked"
    link.symlink_to(evil)
    assert ticket_of(guard(f"{link} create --write", state, repo)) == ""
    assert spent(state) == []


def test_a_subagent_gets_no_ticket_and_leaves_the_approval_for_the_main_session(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    for event in ({"agent_id": "helper-1"}, {"agent_id": "helper-2", "agent_type": "general-purpose"}):
        assert ticket_of(guard("scripts/release-key create --write", state, repo, **event)) == ""
    assert spent(state) == []
    assert len(ticket_of(guard("scripts/release-key create --write", state, repo))) == 32


def test_no_approval_means_no_ticket(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert records(state, "run-ticket") == []


def test_an_approval_of_one_command_gives_no_ticket_for_the_other(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    assert ticket_of(guard(" ".join(f"scripts/{w}" if w == APPLY[0] else w for w in APPLY), state, repo)) == ""
    assert ticket_of(guard("scripts/github-protection-apply --apply --repo ownerlogin/poolhouse-test --strict-tags", state, repo)) == ""
    assert spent(state) == []


def test_an_approval_spoken_in_another_session_gives_the_main_session_no_ticket(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    question, yes, _ = asked(propose(repo, state, *KEY))
    hook_process(answer_event(repo, question, yes, session_id=OTHER), state)
    assert len(records(state, "run-script")) == 1
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


def test_an_approval_is_spent_only_when_the_checkout_still_matches_it(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    (repo / "src" / "code.py").write_text("x = 3\n")
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    git(repo, "checkout", "--", "src/code.py")
    assert len(ticket_of(guard("scripts/release-key create --write", state, repo))) == 32


def test_an_expired_approval_gives_no_ticket(world, monkeypatch):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 16 * 60)
    with pytest.raises(person_auth.NotAuthorized):
        writer.issue("scripts/release-key create --write", str(repo), session=SESSION)
    monkeypatch.setattr(time, "time", real)
    assert len(writer.issue("scripts/release-key create --write", str(repo), session=SESSION)) == 32


def test_the_guard_does_not_stamp_a_command_that_only_names_the_script_in_a_search(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    for command in ("grep -n create scripts/release-key", "git add scripts/release-key", "cat scripts/release-key"):
        done = guard(command, state, repo)
        assert done.returncode == 0 and ticket_of(done) == "", command
    assert spent(state) == []


# -- the scripts that spend the ticket ---------------------------------------------------------


def load(name, monkeypatch, repo):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(ROOT / "scripts" / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", repo)
    return module


def recording_gh(tmp, name="bin"):
    """A `gh` that records its arguments, its standard input and its environment, in a folder of the trusted ones."""
    where, log = tmp / name, tmp / f"{name}-log"
    where.mkdir()
    log.mkdir()
    gh = where / "gh"
    gh.write_text(f'#!/bin/sh\nn=$(ls "{log}" | grep -c "^stdin")\ncat > "{log}/stdin.$n"\necho "$@" > "{log}/argv.$n"\n'
                  f'env > "{log}/env.$n"\n')
    gh.chmod(0o755)
    return where, log


@pytest.fixture
def release(world, monkeypatch):
    """The release-key script on the world's checkout with a trusted fake `gh`; ``(world..., module, gh log)``."""
    tmp, state, repo = world
    module = load("release-key", monkeypatch, repo)
    where, log = recording_gh(tmp)
    monkeypatch.setattr(run_approval, "TRUSTED_DIRS", (str(where), "/usr/bin", "/bin"))
    signing = repo / "signing.py"
    signing.write_text('RELEASE_KEY = ""\n')
    monkeypatch.setattr(module, "SIGNING", signing)
    monkeypatch.setenv("PATH", f"{where}{os.pathsep}{os.environ['PATH']}")
    credentials.set("PRIMER", "x", keychain=True)  # a person provisions the keystore once; an agent run only reads it
    monkeypatch.setenv("CLAUDECODE", "1")
    return tmp, state, repo, module, log


def test_an_approved_agent_run_makes_the_key_without_showing_it_and_pins_the_repository(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    held = (log / "stdin.0").read_bytes()
    assert held.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
    assert held == base64.b64decode(str(credentials.get(rk.ITEM)))
    assert (log / "argv.0").read_text().split() == ["secret", "set", "RELEASE_SIGNING_KEY", "--env", "release", "--repo", SLUG]
    out = capsys.readouterr()
    assert "PRIVATE KEY" not in out.out + out.err
    assert 'RELEASE_KEY = "ssh-ed25519 ' in (repo / "signing.py").read_text()
    assert run_approval.TICKET_ENV not in os.environ


def test_the_ticket_is_spent_by_the_run_and_no_second_run_follows_it(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    monkeypatch.setattr(credentials, "get", lambda *_: None)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert "run-approval propose release-key create --write" in capsys.readouterr().err
    assert not (log / "stdin.1").exists()


def test_without_a_ticket_an_agent_is_refused_with_the_way_to_ask(release, capsys):
    _tmp, _state, _repo, rk, log = release
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    err = capsys.readouterr().err
    assert "for a person" in err and "scripts/hooks/run-approval propose release-key create --write" in err
    assert list(log.iterdir()) == [] and credentials.get(rk.ITEM) is None


@pytest.mark.parametrize("secret", ["", "0" * 32, "f" * 31, "x" * 32, "../" * 8, "a" * 33, "0123456789abcdef0123456789abcdef"])
def test_a_ticket_the_guard_did_not_issue_opens_nothing(release, monkeypatch, secret):
    tmp, state, repo, rk, log = release
    approve(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == [] and credentials.get(rk.ITEM) is None
    assert spent(state) == []


@pytest.mark.parametrize("name", ["POOLHOUSE_RUN_APPROVED", "POOLHOUSE_RUN_ALLOW", "POOLHOUSE_AGENT_APPROVED", "POOLHOUSE_GUARD",
                                  "POOLHOUSE_PUSH_MAIN", "POOLHOUSE_ALLOW_AGENT", "POOLHOUSE_PERSON"])
def test_no_variable_opens_the_scripts_to_an_agent(release, monkeypatch, name):
    tmp, state, repo, rk, log = release
    approve(tmp, state, repo, *KEY)
    monkeypatch.setenv(name, "1" if name != "POOLHOUSE_GUARD" else "off")
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == [] and spent(state) == []


def test_a_ticket_belongs_to_the_session_that_was_asked(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, OTHER) == 1
    assert list(log.iterdir()) == []
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


def test_a_ticket_for_one_command_runs_no_other_and_keeps_its_value(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    for command in (["rotate", "--write"], ["create"], ["show-public"], ["agent"], ["create", "--write", "--write"]):
        monkeypatch.setenv(run_approval.TICKET_ENV, secret)
        assert rk.main(command, NO_TERMINAL, SESSION) == 1, command
    assert list(log.iterdir()) == []
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


def test_a_ticket_for_another_script_does_not_run_this_one(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *APPLY)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_a_ticket_dies_after_a_minute(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 2 * 60)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_a_change_to_the_script_after_the_ticket_voids_it(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    (repo / "scripts" / "release-key").write_text("#!/bin/sh\ncat /dev/null\n")
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_the_origin_moving_after_the_ticket_voids_it(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    git(repo, "remote", "set-url", "origin", "https://github.com/attacker/stolen.git")
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_the_environment_of_an_approved_run_cannot_redirect_the_key(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    evil, evil_log = recording_gh(tmp, "evil")
    for name, value in (("GH_REPO", "attacker/stolen"), ("GH_HOST", "attacker.example"), ("GH_TOKEN", "t0ken"),
                        ("GITHUB_TOKEN", "t0ken"), ("GH_ENTERPRISE_TOKEN", "t0ken"), ("HTTPS_PROXY", "http://attacker.example:3128"),
                        ("XDG_CONFIG_HOME", str(tmp / "evil-config")), ("GH_CONFIG_DIR", str(tmp / "evil-config")),
                        ("SSL_CERT_FILE", str(tmp / "evil.pem")), ("SSH_AUTH_SOCK", str(tmp / "evil.sock")),
                        ("HOME", str(tmp / "evil-home"))):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", f"{evil}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    assert list(evil_log.iterdir()) == []
    seen = dict(line.split("=", 1) for line in (log / "env.0").read_text().splitlines() if "=" in line)
    assert not {"GH_REPO", "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "HTTPS_PROXY", "XDG_CONFIG_HOME",
                "GH_CONFIG_DIR", "SSL_CERT_FILE", "SSH_AUTH_SOCK", run_approval.TICKET_ENV} & set(seen)
    assert str(evil) not in seen["PATH"] and seen["CLAUDECODE"] == "1"
    assert seen["HOME"] == str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())


def test_a_person_at_a_terminal_still_runs_with_no_ticket_and_the_old_arguments(release, monkeypatch):
    _tmp, _state, _repo, rk, log = release
    monkeypatch.delenv("CLAUDECODE")
    assert rk.main(["create"], (True, True), None) == 0
    assert (log / "argv.0").read_text().split() == ["secret", "set", "RELEASE_SIGNING_KEY", "--env", "release"]


def test_every_other_command_of_the_script_stays_a_persons_even_with_a_live_approval(release):
    tmp, state, repo, rk, log = release
    approve(tmp, state, repo, *KEY)
    for command in (["rotate"], ["rotate", "--write"], ["show-public"], ["agent"], ["create"]):
        assert rk.main(command, NO_TERMINAL, SESSION) == 1, command
    assert list(log.iterdir()) == [] and spent(state) == []


# -- the protection script ---------------------------------------------------------------------


def before_state():
    replies = {**GOOD}
    replies[f"repos/{REPO}/rulesets"] = [*GOOD[f"repos/{REPO}/rulesets"], {"id": 4, "name": "default"}]
    replies[f"repos/{REPO}/rulesets/4"] = ruleset("default", "branch", ["~DEFAULT_BRANCH"], [{"type": "update"}])
    replies[f"repos/{REPO}/environments/release/deployment-branch-policies"] = {"branch_policies": []}
    return replies


@pytest.fixture
def protect(world, monkeypatch):
    """The protection script on the world's checkout, a `gh` serving a repository, the agent marker set; ``(tmp, state, repo, module, log)``."""
    tmp, state, repo = world
    module = load("github-protection-apply", monkeypatch, repo)
    log = serve(tmp, monkeypatch, before_state())
    monkeypatch.setattr(module, "identity", lambda _repo: (True, "agent identity: ready"))
    module.checked_with = []
    monkeypatch.setattr(module, "check", lambda repo, args: module.checked_with.append(module.environment_of_run()) or 0)
    monkeypatch.setattr(run_approval, "TRUSTED_DIRS", (str(tmp / "bin"), "/usr/bin", "/bin"))
    monkeypatch.setattr(run_approval, "KEPT", (*run_approval.KEPT, "GH_LOG", "GH_REPLIES", "GH_FAIL", "GH_NOAUTH"))
    monkeypatch.setenv("CLAUDECODE", "1")
    return tmp, state, repo, module, log


def writes(log):
    return [(m, p) for m, p, *_ in (line.split(" ", 2) for line in log.read_text().splitlines()) if m != "GET"]


def test_an_agent_may_look_at_what_the_apply_would_change_with_no_approval(protect, capsys):
    _tmp, state, _repo, ap, log = protect
    assert ap.main(["--repo", REPO], NO_TERMINAL, SESSION) == 0
    out = capsys.readouterr().out
    assert "== release environment" in out and "== remove the ruleset named default" in out
    assert writes(log) == [] and not (state / "github-protection").exists()


def test_an_agent_that_applies_with_no_approval_is_refused_before_any_request_and_told_how_to_ask(protect, capsys):
    _tmp, state, _repo, ap, log = protect
    assert ap.main(["--repo", REPO, "--apply"], NO_TERMINAL, SESSION) == 1
    assert log.read_text() == "" and not (state / "github-protection").exists()
    err = capsys.readouterr().err
    assert "for a person" in err and f"run-approval propose github-protection-apply --apply --repo {REPO}" in err


def test_an_approved_agent_run_applies_to_the_origins_repository_in_order(protect, monkeypatch):
    tmp, state, repo, ap, log = protect
    secret = ticket_for(tmp, state, repo, *APPLY)
    monkeypatch.setenv("GH_REPO", "attacker/stolen")
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert ap.main(["--apply", "--repo", REPO], NO_TERMINAL, SESSION) == 0
    (children,) = ap.checked_with
    assert children["PATH"].startswith(str(tmp / "bin")) and "GH_REPO" not in children and run_approval.TICKET_ENV not in children
    base = f"repos/{REPO}"
    done = writes(log)
    assert done[:2] == [("PUT", f"{base}/environments/release"), ("POST", f"{base}/environments/release/deployment-branch-policies")]
    assert ("DELETE", f"{base}/rulesets/4") in done and done[-1] == ("PATCH", base)
    assert all(base in path for _m, path in done)
    assert len(list((state / "github-protection").glob("backup-*.json"))) == 1


def test_an_approval_of_the_apply_covers_neither_a_restore_nor_another_repository(protect, monkeypatch):
    tmp, state, repo, ap, log = protect
    secret = ticket_for(tmp, state, repo, *APPLY)
    for command in (["--apply", "--repo", "attacker/stolen"], ["--apply"], ["--restore", str(tmp / "b.json"), "--apply"],
                    ["--apply", "--repo", REPO, "--strict-tags"], ["--apply", "--repo", REPO, "--owner-review"]):
        monkeypatch.setenv(run_approval.TICKET_ENV, secret)
        assert ap.main(command, NO_TERMINAL, SESSION) == 1, command
    assert log.read_text() == ""


def test_the_release_environment_is_made_even_before_the_agent_identity_is(protect, monkeypatch, capsys):
    _tmp, _state, _repo, ap, log = protect
    monkeypatch.setattr(ap, "identity", lambda _repo: (False, "agent identity: not set up; run scripts/agent-identity setup"))
    monkeypatch.delenv("CLAUDECODE")
    assert ap.main(["--repo", REPO, "--apply"], (True, True)) == 1
    base = f"repos/{REPO}"
    assert writes(log) == [("PUT", f"{base}/environments/release"), ("POST", f"{base}/environments/release/deployment-branch-policies")]
    err = capsys.readouterr().err
    assert "scripts/agent-identity setup" in err and "release environment" in err


# -- the error message that named a flag the script does not have -----------------------------------


def test_the_hint_for_a_missing_environment_names_a_command_the_script_accepts(release, monkeypatch, capsys):
    tmp, _state, _repo, rk, _log = release
    failing = tmp / "failing"
    failing.mkdir()
    (failing / "gh").write_text('#!/bin/sh\necho "gh: HTTP 404 environments/release/secrets/public-key" >&2\nexit 1\n')
    (failing / "gh").chmod(0o755)
    monkeypatch.setattr(run_approval, "TRUSTED_DIRS", (str(failing), "/usr/bin", "/bin"))
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("PATH", f"{failing}{os.pathsep}{os.environ['PATH']}")
    assert rk.main(["create"], (True, True)) == 1
    err = capsys.readouterr().err
    command = next(part for part in err.split("`") if part.startswith("scripts/github-protection-apply"))
    assert "--identity-ready" not in err
    ap = load("github-protection-apply", monkeypatch, tmp)
    with pytest.raises(SystemExit) as helped:
        ap.main(["--help"])
    assert helped.value.code == 0
    flags = [word for word in shlex.split(command) if word.startswith("--")]
    assert flags and all(flag in capsys.readouterr().out for flag in flags)


# -- who can reach the writers --------------------------------------------------------------------


def test_the_new_code_is_guarded_as_the_person_record_code_is():
    for path in ("src/poolhouse/workspace/run_approval.py", "src/poolhouse/workspace/run_hook.py", "scripts/hooks/run-approval"):
        assert protectedwrites.guarded(str(ROOT / path)), path
    assert not protectedwrites.guarded(str(ROOT / "scripts" / "release-key"))


@pytest.mark.parametrize("command", [
    "scripts/hooks/run-approval ticket", "python3 scripts/hooks/run-approval ticket", "sh scripts/hooks/run-approval", "scripts/hooks/run-approval",
    "python3 -c 'from poolhouse.workspace import run_hook'", "python3 -c 'from poolhouse.workspace.run_hook import issue'",
    "python3 -c 'from poolhouse.workspace import run_approval'", "python3 -m poolhouse.workspace.run_hook",
    "python3 -c 'import poolhouse.workspace.run_approval'"])
def test_an_agent_command_cannot_reach_the_ticket_or_the_writers(world, command):
    _, state, repo = world
    for env in ({}, {"POOLHOUSE_GUARD": "off"}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        done = guard(command, state, repo, env=env)
        assert done.returncode == 2 and "person record" in done.stderr, (command, env)


def test_an_agent_may_ask_the_question_and_run_the_covered_commands(world):
    _, state, repo = world
    for command in ("scripts/hooks/run-approval propose release-key create --write", "scripts/release-key create --write",
                    f"scripts/github-protection-apply --repo {SLUG}"):
        assert guard(command, state, repo).returncode == 0, command


def test_the_real_scripts_are_not_mistaken_by_the_guard_for_something_that_names_the_person_record(world):
    _, state, _ = world
    for command in ("scripts/release-key show-public", "scripts/github-protection-apply --repo ownerlogin/poolhouse-test",
                    "scripts/hooks/run-approval propose release-key create --write"):
        done = guard(command, state, ROOT)
        assert done.returncode == 0, (command, done.stderr)


def test_only_guarded_code_writes_the_person_record():
    found = set()
    for path in sorted((ROOT / "src" / "poolhouse").rglob("*.py")):
        if "person_record" in path.read_text(encoding="utf-8") and path.name != "person_record.py":
            found.add(path.relative_to(ROOT).as_posix())
    assert found and all(protectedwrites.guarded(str(ROOT / one)) for one in found), found


def test_the_log_still_verifies_after_every_step_of_a_run(release, monkeypatch):
    tmp, state, repo, rk, _log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, secret)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    assert person_store.open_log().verify().ok
    kinds = [(r["type"], r.get("kind") or r.get("state")) for r in rows(state) if r["type"] in ("authorization", "transition")]
    assert kinds == [("authorization", "run-script"), ("transition", "used"), ("authorization", "run-ticket"), ("transition", "used")]
