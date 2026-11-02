"""run-script: the owner's one-time approval of one agent run of a release script, bound to the exact command, repository and tree."""

from __future__ import annotations

import hmac
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home, person
from poolhouse.sentinel.events import EventLog
from poolhouse.workspace import person_auth, person_store, person_targets

__all__ = ["KIND", "RUNS", "TICKET", "TICKET_ENV", "TICKET_MINUTES", "Approval", "Facts", "NotARun", "admit", "approved",
           "child_environment", "command", "echo_for", "facts", "options", "parse", "propose", "question", "redeem", "target"]

KIND = "run-script"
TICKET = "run-ticket"
TICKET_ENV = "POOLHOUSE_RUN_TICKET"
TICKET_MINUTES = 1
"""The store counts whole minutes, and the Bash tool starts the script seconds after the guard hands the ticket over."""
CHECKED = ("scripts", "src")
"""The folders whose files are the code the approved script runs; any change in them voids an approval."""
TRUSTED_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin", "/usr/local/bin")
"""Where the system and Homebrew keep `gh` and `ssh-keygen`; an approved run finds its tools nowhere else."""
KEPT = ("USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR")
"""The variables an approved run passes on besides the agent markers, POOLHOUSE_ settings and the account's home; none redirects `gh`."""
ECHO = re.compile(r"authorize run-script (?P<target>\S+) (?P<minutes>\d+)m x(?P<uses>\d+) #(?P<tag>[0-9a-f]{12})")
TARGET = re.compile(r"(?P<top>[^|\s#]+)\|(?P<name>[a-z-]+)\|(?P<args>[^|\s#]+)\|(?P<slug>[\w.-]+/[\w.-]+)\|(?P<tree>[0-9a-f]{40})")
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SECRET = re.compile(r"[0-9a-f]{32}")
SIMPLE = re.compile(r"[A-Za-z0-9_./ -]+")
GITHUB = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?/?")
OPTIONS = ("--owner-review", "--strict-tags")


class NotARun(ValueError):
    """A command that is not one of the runs an approval can cover."""


def _create_write(args: list[str], _slug: str) -> tuple[str, ...] | None:
    return ("create", "--write") if args == ["create", "--write"] else None


def _apply_here(args: list[str], slug: str) -> tuple[str, ...] | None:
    """The arguments of `github-protection-apply` that name the repository and apply, in one fixed order."""
    if args.count("--apply") != 1 or args.count("--repo") != 1 or args[args.index("--repo") + 1:][:1] != [slug]:
        return None
    rest = [a for a in args if a not in ("--apply", "--repo", slug)]
    if len(args) != 3 + len(rest) or any(a not in OPTIONS or rest.count(a) > 1 for a in rest):
        return None
    return ("--apply", "--repo", slug, *(o for o in OPTIONS if o in rest))


@dataclass(frozen=True, slots=True)
class Script:
    """A script an agent may run on the owner's approval: which arguments an approval covers and what the run does."""

    form: Callable[[list[str], str], tuple[str, ...] | None]
    does: str


RUNS = {
    "release-key": Script(_create_write,
                          "makes the release signing key, keeps it in this device's keystore, stores it as the release "
                          "environment's RELEASE_SIGNING_KEY secret of {slug} and sets RELEASE_KEY in "
                          "src/poolhouse/fleet/signing.py. The key is never printed."),
    "github-protection-apply": Script(_apply_here,
                                      "applies the GitHub protection target of docs/github-protection.md to {slug}: the "
                                      "rulesets, the release environment and the Actions settings. It saves the current "
                                      "settings first and prints how to restore them."),
}


@dataclass(frozen=True, slots=True)
class Facts:
    """What git and the checkout say about one run: where, which script and arguments, the repository, the commit and its tree."""

    top: str
    name: str
    args: tuple[str, ...]
    slug: str
    remote: str
    sha: str
    tree: str


@dataclass(frozen=True, slots=True)
class Approval:
    """An agent's run the owner approved: the repository it is pinned to and the environment its children get."""

    slug: str
    environment: dict[str, str]


def parse(text: str) -> tuple[str, list[str]] | None:
    """The program and arguments of ``text`` when it is one plain command of words, paths and flags that names a covered script, else None."""
    if not SIMPLE.fullmatch(text.strip()):
        return None
    words = text.split()
    return (words[0], words[1:]) if words and Path(words[0]).name in RUNS else None


def _slug(url: str) -> str:
    found = GITHUB.fullmatch(url.strip())
    return found["slug"] if found else ""


def _clean(top: str) -> bool:
    """Whether git can say that the files of the checked folders match the commit, with no file added."""
    return (person_targets.git_ok(top, "diff", "--quiet", "HEAD", "--", *CHECKED)
            and person_targets.git_ok(top, "ls-files", "--others", "--exclude-standard", "--", *CHECKED)
            and not person_targets.run_git(top, "ls-files", "--others", "--exclude-standard", "--", *CHECKED))


def facts(where: str | Path, script: str | Path, args: list[str] | tuple[str, ...]) -> Facts | None:
    """The `Facts` of running ``script`` with ``args`` from the checkout holding ``where``; None when it is no covered run or git cannot vouch for it."""
    name = Path(script).name
    entry = RUNS.get(name)
    top = person_targets.run_git(where, "rev-parse", "--show-toplevel")
    if entry is None or not top:
        return None
    top = os.path.realpath(top)
    if os.path.realpath(Path(where) / script) != os.path.realpath(Path(top, "scripts", name)):
        return None
    remote = person_targets.inspect(top).remote
    slug = _slug(person_targets.run_git(top, "config", "--get", f"remote.{remote}.url"))
    sha = person_targets.run_git(top, "rev-parse", "--verify", "HEAD^{commit}")
    tree = person_targets.run_git(top, "rev-parse", "HEAD^{tree}")
    covered = entry.form(list(args), slug) if slug else None
    if covered is None or any(c.isspace() or c in "|#" for c in top):
        return None
    if not (FULL_SHA.fullmatch(sha) and FULL_SHA.fullmatch(tree) and _clean(top)):
        return None
    return Facts(top, name, covered, slug, remote, sha, tree)


def target(found: Facts) -> str:
    """The target an approval is bound to: checkout, script, arguments, repository and tree."""
    return f"{found.top}|{found.name}|{','.join(found.args)}|{found.slug}|{found.tree}"


def command(found: Facts) -> str:
    """The command line the approval covers."""
    return " ".join([f"scripts/{found.name}", *found.args])


def question(found: Facts) -> str:
    """The approval question's text, made only of what git and the registry report."""
    return (f"Run {command(found)}?\nRepository: {found.slug} (remote {found.remote})\nCheckout: {found.top}\n"
            f"Commit: {found.sha}, with scripts and src unchanged\n"
            f"What it does: {RUNS[found.name].does.format(slug=found.slug)}\n"
            "The agent runs it once, as typed above; it cannot change the command, the commit or the repository after your answer.")


def options(found: Facts) -> tuple[str, str]:
    """The two answers: the approval is the first."""
    return f"Approve {command(found)}", "Do not run it"


def _tag(log: EventLog, where: str, minutes: int, uses: int) -> str:
    return log.mac(f"{KIND}|{where}|{minutes}|{uses}".encode())[:12]


def echo_for(log: EventLog, found: Facts, minutes: int = person_store.DEFAULT_MINUTES, uses: int = 1) -> str:
    """The question's final line: kind, target, life and uses, with a tag made under the store's key."""
    return f"authorize {KIND} {target(found)} {minutes}m x{uses} #{_tag(log, target(found), minutes, uses)}"


def approved(log: EventLog, text: str, label: str, checkout: person_targets.Checkout) -> Facts | None:
    """The `Facts` an answered question approves: a tagged echo made by this store, the question git and the registry now render, and the approving ``label``."""
    *body, last = text.rstrip().splitlines() or [""]
    echoed = ECHO.fullmatch(last.strip())
    if echoed is None or int(echoed["minutes"]) != person_store.DEFAULT_MINUTES or int(echoed["uses"]) != 1:
        return None
    if not hmac.compare_digest(_tag(log, echoed["target"], person_store.DEFAULT_MINUTES, 1), echoed["tag"]):
        return None
    parts = TARGET.fullmatch(echoed["target"])
    if parts is None or person_targets.inspect(parts["top"]).project != checkout.project:
        return None
    found = facts(parts["top"], Path(parts["top"], "scripts", parts["name"]), parts["args"].split(","))
    if found is None or target(found) != echoed["target"] or "\n".join(body).strip() != question(found):
        return None
    return found if label == options(found)[0] else None


def propose(argv: list[str], cwd: str, log: EventLog | None = None) -> tuple[int, str]:
    """The exit code and text of `run-approval propose NAME ARGS...`: the question, its echo line and the two answers."""
    top = person_targets.run_git(cwd, "rev-parse", "--show-toplevel")
    found = facts(cwd, Path(top, "scripts", argv[0]), argv[1:]) if argv and top else None
    if found is None:
        return 1, ("git cannot show the facts of that run: it must be `release-key create --write` or `github-protection-apply "
                   "--apply --repo OWNER/NAME` (the origin's repository), in a checkout whose scripts and src hold no change")
    log = log or person_store.open_log()
    yes, no = options(found)
    return 0, f"{question(found)}\n{echo_for(log, found)}\n\noptions: {yes} | {no}"


def redeem(found: Facts, secret: str, session: str | None = None, log: EventLog | None = None) -> str:
    """Spend the ticket ``secret`` for the run ``found``; raises `person_auth.NotAuthorized` when no live ticket of this session matches."""
    log = log or person_store.open_log()
    if not SECRET.fullmatch(secret):
        raise person_auth.NotAuthorized("no ticket for this run")
    who = person_auth.Identity(person_auth.session_of_ancestry(log) if session is None else session)
    return person_auth.consume(TICKET, f"{target(found)}#{secret}", who, log=log)


def child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment an approved run gives its children: the account's own home, the agent markers, POOLHOUSE_ settings and the system tool folders."""
    found = os.environ if source is None else source
    kept = {k: v for k, v in found.items() if k in KEPT or k in person.AGENT_MARKERS or k.startswith("POOLHOUSE_")}
    kept.pop(TICKET_ENV, None)
    kept["HOME"] = str(home.account_roots()[0].parent)
    kept["PATH"] = os.pathsep.join(TRUSTED_DIRS)
    return kept


def _how(found: Facts) -> str:
    return (f"the owner can approve this exact command for an agent: run scripts/hooks/run-approval propose {found.name} "
            f"{' '.join(found.args)}, ask its question with AskUserQuestion word for word, and run the command again after the answer")


def admit(script: str | Path, args: list[str], terminal: tuple[bool, bool] | None = None,
          session: str | None = None) -> Approval | None:
    """None when a person runs ``script``; an `Approval` when the owner's answer covers this agent's run; else raises `person.HumanRequired`."""
    secret = os.environ.pop(TICKET_ENV, "")
    try:
        person.require_person(f"scripts/{Path(script).name}", terminal)
    except person.HumanRequired as refused:
        found = facts(Path(script).parent, script, args)
        if found is None:
            raise
        try:
            redeem(found, secret, session)
        except (person_auth.NotAuthorized, person_store.Unreadable) as missing:
            raise person.HumanRequired(f"{refused}; {_how(found)}") from missing
        return Approval(found.slug, child_environment())
    return None
