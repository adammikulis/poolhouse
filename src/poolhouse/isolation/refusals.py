"""The sentences a coding agent's launch is refused with, and the two exceptions that carry them.

Each sentence names a fact about this computer or about the launch. None names a command for a person to run, an argument that
would widen a session, or a step that waits for anyone: where the remedy is a step of Poolhouse's own installation the sentence says
so (docs/design-isolated-launcher.md, section 9, and the rule "Zero friction").
"""

from __future__ import annotations

__all__ = ["DECISION_LINES", "KIND_PHRASES", "SENTENCES", "IsolationFailed", "IsolationRefused", "refusal_line", "sentence"]


class IsolationRefused(RuntimeError):
    """The launch is refused before anything starts: this computer cannot confine the coding agent, or the launch asks for what no rule allows."""


class IsolationFailed(RuntimeError):
    """The proof found the confinement open: the first attempt that got through, the guarantee it belongs to and the profile it ran under."""

    def __init__(self, attempt: str, guarantee: str, digest: str, detail: str = "") -> None:
        self.attempt, self.guarantee, self.digest, self.detail = attempt, guarantee, digest, detail
        super().__init__(sentence("proof_failed", attempt=attempt))


SENTENCES: dict[str, str] = {
    "no_sandbox_tool": "This Mac cannot confine coding agents: its built-in sandbox tool is missing.",
    "nested": ("This command is already inside a sandbox, and a sandbox cannot start inside another; start it from a terminal or "
               "from a session that is not sandboxed."),
    "no_bubblewrap": "This computer cannot confine coding agents until Poolhouse's installation has added bubblewrap.",
    "no_disable_userns": ("The installed bubblewrap cannot stop a coding agent from starting its own sandboxes (it lacks the option "
                          "that turns that off)."),
    "userns_off": ("This computer's security settings stop coding agents from being confined (unprivileged user namespaces are "
                   "off)."),
    "apparmor_restricts": ("This computer's security rules block the sandbox tool until Poolhouse's installation has added its "
                           "permission, so the coding agent was not started."),
    "apparmor_changed": ("The permission installed for the sandbox tool is not the one Poolhouse ships; Poolhouse's installation "
                         "restores it, so the coding agent was not started."),
    "identifiers_readable": ("This Mac's serial number and hardware identifiers are readable from inside the safety box, so the "
                             "coding agent was not started."),
    "wsl1": "Coding agents need WSL 2; this is WSL 1.",
    "windows_drive": "This folder is on a Windows drive; coding agents can only be confined for folders inside WSL.",
    "windows_network": "This Windows computer cannot keep a coding agent's network to the model alone; run it in WSL 2.",
    "stage_too_small": ("This computer has {free} MB free for the working copy and this project needs {need} MB; free some space and "
                        "the agent will start."),
    "no_git": "Coding agents need git, and none was found that Poolhouse can trust.",
    "not_a_repository": "A coding agent works on a copy of a git project, and this folder is not one.",
    "on_not_loopback": "A coding agent can only use a model server on this computer; {url} is not.",
    "interactive": "Interactive mode is not available yet; use the coding agent's print mode.",
    "service_failed": "The coding agent's helper ({name}) could not start, so the agent was not started.",
    "proof_failed": ("Poolhouse could not confirm that the safety box holds on this computer (it allowed {attempt}), so the coding "
                     "agent was not started."),
    "confined_caller": "Refused: a coding session cannot change what another coding session may reach; this process runs inside one.",
    "not_the_lead": "Refused: only the session that launches coding agents for the person can widen what one may reach.",
    "held_back": "Refused: widening a coding session is held back from agents on this computer.",
}
"""The refusal sentences of section 9, one per condition, and the refusals of the launch gate (8.3). A brace names a fact of the launch."""

KIND_PHRASES: dict[str, str] = {
    "read": "a read of another folder",
    "toolchain": "a toolchain folder",
    "net": "a network host",
    "publish": "a change to a file of the project",
    "in-place": "changing the project in place",
}
"""What each kind of widening is called in the one refusal form: it names the kind and never the subject the session chose."""

DECISION_LINES: dict[str, str] = {
    "read": "This coding session can read {subject} (it cannot change it).",
    "toolchain": "This coding session can run the programs in {subject}.",
    "net": "This coding session can reach {subject}, and can send to it as well as receive from it; bytes sent are cut at a bound.",
    "publish": "This coding session can change the file {subject} in your project; the change is applied as the agent made it.",
    "in-place": ("This coding session changes your files directly in {subject}. Files like .env in that folder are readable to it, "
                 "and nothing checks the changes before they land."),
}
"""The line a granted widening is logged and printed with (8.2)."""


def sentence(key: str, **facts: object) -> str:
    """The sentence ``key`` with the facts of the launch put in."""
    return SENTENCES[key].format(**{name: str(value) for name, value in facts.items()})


def refusal_line(kind: str) -> str:
    """The one form a refused widening takes: it names the kind of widening and never a person, a command or a subject the session chose."""
    return f"Refused: {KIND_PHRASES[kind]} is not allowed for this coding session."
