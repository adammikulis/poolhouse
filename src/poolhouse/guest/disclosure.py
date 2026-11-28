"""What each side is told, in plain words, shown word for word by every screen and command. Nothing here may promise more than the
code and the operating system give."""

from __future__ import annotations

__all__ = ["FOR_GUEST", "FOR_HOST"]

FOR_GUEST: tuple[str, ...] = (
    "What you send travels encrypted between your computer and theirs. The network in between, including Tailscale, can see "
    "that you connect, when and how much, but cannot read it.",
    "Poolhouse on their computer, unchanged, does not save, log or show your prompts or the replies. Their screen shows counts only.",
    "Whoever controls their computer can still read everything: by changing the software, by reading the memory of the running "
    "programs, or by recording what the model computes. You cannot check from here what is running there.",
    "While you are working, your conversation is held in their computer's memory. Their operating system may copy memory to its "
    "disk (swap, hibernation or a crash report). Poolhouse does not lock that memory and cannot be sure it is wiped afterwards.",
    "Whatever your tools put into a prompt is sent to them, including the contents of files they read.",
    "Replies come from their computer. They can be changed or invented, including commands and code. Run coding agents only in a "
    "sandbox on your computer, and read changes before you run them. A program you point at this connection yourself decides what "
    "a reply is allowed to do.",
)

FOR_HOST: tuple[str, ...] = (
    "A guest can ask the model you share, and nothing else: no files, projects, jobs, board or settings on this computer.",
    "Poolhouse does not save, log or show what a guest sends or receives. You see counts only.",
    "A guest's conversation is held in this computer's memory while they are active. It is given back when they have been idle, "
    "when you remove them and when sharing stops.",
    "Each active guest uses a model server of their own, so their share needs memory beside yours. A model that fits only once "
    "cannot be shared while you are using it: your work is never stopped to make room.",
    "A guest can keep this computer's processor busy within the limits you set.",
)
