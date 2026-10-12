"""scripts/github-protection on a device that holds no push credential: what its git configuration can still push with."""

from __future__ import annotations

import copy
import os

import pytest

from tests.github_support import GOOD, REPO, codes, git_checkout, serve, sign_out

pytest_plugins = ["github_support"]
pytestmark = pytest.mark.usefixtures("device")

HTTPS = f"https://github.com/{REPO}.git"
SSH = f"git@github.com:{REPO}.git"
PUSH_SSH = ("config", "remote.origin.pushurl", SSH)
SIGNED = ("signed out", "signed in")


def verdict(out):
    return out.strip().splitlines()[-1]


@pytest.fixture
def judged(gp, tmp_path, monkeypatch, capsys):
    """A bare `--check --agent` on a device with a git configuration: (status, stdout, gh's log)."""

    def run(origin=HTTPS, config=(), signed=False, flags=()):
        monkeypatch.setattr(gp, "CHECKOUT", git_checkout(tmp_path / "checkout", origin, *config))
        replies = copy.deepcopy(GOOD)
        replies[f"repos/{REPO}"]["permissions"].update(push=False)
        log = serve(tmp_path, monkeypatch, replies)
        if not signed:
            sign_out(monkeypatch)
        status = gp.main(["--check", "--agent", *flags])
        return status, capsys.readouterr().out, log.read_text().splitlines()

    return run


# -- the repository comes from the origin remote's address, judged by its host --------------

@pytest.mark.parametrize(("origin", "expected"), [
    (HTTPS, REPO),
    (f"https://github.com/{REPO}", REPO),
    (f"https://github.com/{REPO}/", REPO),
    (f"https://www.github.com/{REPO}.git", REPO),
    (f"https://GitHub.com/{REPO}.git", REPO),
    (f"https://noreply@github.com/{REPO}.git", REPO),
    (SSH, REPO),
    (f"ssh://git@github.com/{REPO}.git", REPO),
    (f"ssh://git@github.com:22/{REPO}.git", REPO),
    ("https://evil.example/x?github.com/o/n", ""),
    ("https://notgithub.com/o/n", ""),
    ("https://github.com/a?b=1/n", ""),
    ("https://github.com@evil.example/o/n", ""),
    ("https://github.com.evil.example/o/n", ""),
    ("https://github.com/a/b/c", ""),
    ("git@evil.example:github.com/o/n.git", ""),
    ("ssh://git@evil.example:22/github.com/o/n", ""),
])
def test_the_repository_is_the_one_the_origin_names_on_github_com_only(
        gp, tmp_path, monkeypatch, origin, expected):
    monkeypatch.setattr(gp, "CHECKOUT", git_checkout(tmp_path / "checkout", origin))
    assert gp.origin_repo() == expected


def test_an_origin_that_is_not_on_github_leaves_no_repository(gp, tmp_path, monkeypatch, capsys):
    origin = "https://evil.example/x?github.com/o/n"
    monkeypatch.setattr(gp, "CHECKOUT", git_checkout(tmp_path / "checkout", origin))
    serve(tmp_path, monkeypatch, GOOD)
    sign_out(monkeypatch)
    assert gp.main(["--check", "--agent"]) == 2
    assert "no repository" in capsys.readouterr().err


# -- a device where gh holds no credential, and one where it is read-only --------------------

def test_a_device_with_no_credential_at_all_leaves_no_finding(judged):
    status, out, calls = judged()
    assert (status, verdict(out)) == (0, "0 findings"), out
    assert calls == ["GET rate_limit"]


def test_a_signed_out_lander_is_not_cleared(judged):
    status, out, _ = judged(flags=("--lander",))
    assert status == 1 and "repo.read" in codes(out)


FLAGGED = {
    "an ssh origin": {"origin": SSH},
    "an ssh:// origin": {"origin": f"ssh://git@github.com/{REPO}.git"},
    "an ssh:// origin with a port": {"origin": f"ssh://git@github.com:22/{REPO}.git"},
    "a token as the username": {"origin": f"https://noreply@github.com/{REPO}.git"},
    "a username and password": {"origin": f"https://x-access-token:noreply@github.com/{REPO}.git"},
    "an ssh push url": {"config": (PUSH_SSH,)},
    "an ssh push rewrite": {"config": (
        ("config", "url.ssh://git@github.com/.pushInsteadOf", "https://github.com/"),)},
    "an ssh push default": {"config": (
        ("remote", "add", "upstream", SSH), ("config", "remote.pushDefault", "upstream"))},
    "an ssh branch push remote": {"config": (
        ("remote", "add", "upstream", SSH), ("config", "branch.trunk.pushRemote", "upstream"))},
    "an authorization header": {"config": (
        ("config", "http.extraHeader", "Authorization: basic Zm9v"),)},
    "an authorization header for github.com": {"config": (
        ("config", "http.https://github.com/.extraHeader", "authorization: basic Zm9v"),)},
    "an askpass program": {"config": (("config", "core.askPass", "/usr/bin/true"),)},
}
CLEAN = {
    "an https origin": {},
    "an ssh origin that pushes over https": {"origin": SSH, "config": (
        ("config", "remote.origin.pushurl", HTTPS),)},
    "a push default that is https": {"config": (
        ("remote", "add", "upstream", HTTPS), ("config", "remote.pushDefault", "upstream"))},
    "a header that is not authorization": {"config": (("config", "http.extraHeader", "X-Request: 1"),)},
    "an authorization header for another host": {"config": (
        ("config", "http.https://example.org/.extraHeader", "Authorization: basic Zm9v"),)},
    "an authorization header that was reset": {"config": (
        ("config", "--add", "http.extraHeader", "Authorization: basic Zm9v"),
        ("config", "--add", "http.extraHeader", ""))},
    "an empty askpass": {"config": (("config", "core.askPass", ""),)},
}


@pytest.mark.parametrize("signed", [False, True], ids=SIGNED)
@pytest.mark.parametrize("what", FLAGGED)
def test_a_device_whose_git_may_still_push_is_unverified(judged, what, signed):
    status, out, _ = judged(signed=signed, **FLAGGED[what])
    assert status == 1 and codes(out) == {"credential.unverified"}, out
    for value in ("Zm9v", "noreply", "x-access-token"):
        assert value not in out


@pytest.mark.parametrize("signed", [False, True], ids=SIGNED)
@pytest.mark.parametrize("what", CLEAN)
def test_a_device_whose_git_cannot_push_has_no_finding(judged, what, signed):
    status, out, _ = judged(signed=signed, **CLEAN[what])
    assert (status, verdict(out)) == (0, "0 findings"), out


@pytest.mark.parametrize("signed", [False, True], ids=SIGNED)
@pytest.mark.parametrize("name", ["GIT_ASKPASS", "SSH_ASKPASS"])
def test_an_askpass_program_in_the_environment_is_unverified(judged, monkeypatch, name, signed):
    monkeypatch.setenv(name, "/usr/bin/true")
    status, out, _ = judged(signed=signed)
    assert status == 1 and codes(out) == {"credential.unverified"}, out


@pytest.mark.parametrize("signed", [False, True], ids=SIGNED)
def test_a_netrc_entry_for_github_com_is_unverified_and_no_value_is_shown(judged, device, signed):
    (device / ".netrc").write_text("machine example.org login a password b\n"
                                   "machine github.com login hunter2 password hunter3\n")
    status, out, _ = judged(signed=signed)
    assert status == 1 and codes(out) == {"credential.unverified"}, out
    assert "hunter" not in out


@pytest.mark.parametrize("signed", [False, True], ids=SIGNED)
def test_a_netrc_for_other_machines_is_no_finding(judged, device, signed):
    (device / ".netrc").write_text("machine example.org login a password b\n")
    assert judged(signed=signed)[0] == 0


def test_an_unverified_finding_points_at_a_heading(judged):
    out = judged(origin=SSH)[1]
    line = next(ln for ln in out.splitlines() if ln.startswith("credential.unverified"))
    assert line.rsplit("#", 1)[1] == "push-credential"


def test_a_device_runs_neither_ssh_nor_a_credential_helper_to_be_judged(judged, monkeypatch, tmp_path):
    probes, ran = tmp_path / "probes", tmp_path / "ran"
    probes.mkdir()
    for name in ("ssh", "helper"):
        (probes / name).write_text(f"#!/bin/sh\necho {name} >> {ran}\n")
        (probes / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{probes}{os.pathsep}{os.environ['PATH']}")
    status, out, _ = judged(origin=SSH, config=(("config", "--add", "credential.helper", str(probes / "helper")),))
    assert status == 1 and codes(out) == {"credential.unverified"}, out
    assert not ran.exists()


# -- credential helpers: git's own order and scope ------------------------------------------

def helpers(*values, scope=""):
    key = f"credential.{scope}.helper" if scope else "credential.helper"
    return tuple(("config", "--add", key, value) for value in values)


def test_a_credential_helper_on_a_signed_out_device_is_unverified(judged):
    status, out, _ = judged(config=helpers("store"))
    assert status == 1 and codes(out) == {"credential.unverified"}, out
    assert "credential helper" in out and "whether it holds a token" in out


def test_the_macos_keychain_helper_alone_is_unverified_in_plain_words(judged):
    status, out, _ = judged(config=helpers("osxkeychain"))
    assert status == 1 and codes(out) == {"credential.unverified"}, out
    assert "macOS Keychain" in out and "cannot tell whether" in out


@pytest.mark.parametrize(("config", "flagged"), [
    pytest.param(helpers("osxkeychain", ""), False, id="a later reset clears an earlier helper"),
    pytest.param(helpers("", "store"), True, id="a reset then a helper leaves the helper"),
    pytest.param(helpers("osxkeychain", "", "store"), True, id="a reset between two leaves the second"),
    pytest.param(helpers("store", scope="https://example.org"), False, id="scoped to another host"),
    pytest.param(helpers("store", scope="https://github.com"), True, id="scoped to github.com"),
    pytest.param(helpers("store") + helpers("", scope="https://example.org"), True,
                 id="a reset scoped to another host clears nothing"),
    pytest.param(helpers("store") + helpers("", scope="https://github.com"), False,
                 id="a reset scoped to github.com clears the unscoped helper"),
])
def test_the_helpers_that_count_are_those_git_would_ask_for_github_com(judged, config, flagged):
    status, out, _ = judged(config=config)
    assert (status, codes(out)) == ((1, {"credential.unverified"}) if flagged else (0, set())), out


@pytest.mark.parametrize("value", ["osxkeychain", "!f() { echo password=$GH_TOKEN; }; f"])
def test_a_helper_is_not_judged_when_gh_is_signed_in(judged, value):
    status, out, _ = judged(signed=True, config=helpers("", value))
    assert (status, verdict(out)) == (0, "0 findings"), out


# -- the repository named with --repo -------------------------------------------------------

@pytest.mark.parametrize(("origin", "status"), [(HTTPS, 0), (SSH, 1)], ids=["clean", "flagged"])
def test_a_repo_named_other_than_the_checkouts_is_said_to_be_judged_by_the_checkout(judged, origin, status):
    got, out, _ = judged(origin=origin, flags=("--repo", "someone/else"))
    assert "judged the git configuration of this checkout, not someone/else's" in out
    assert got == status


def test_the_repo_the_checkout_names_needs_no_such_note(judged):
    assert "judged" not in judged(flags=("--repo", REPO))[1]


def test_a_signed_in_device_is_not_told_which_configuration_was_judged(judged):
    assert "judged the git configuration" not in judged(signed=True, flags=("--repo", "someone/else"))[1]
