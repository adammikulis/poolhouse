"""The app's question about signing Poolhouse's macOS apps with a certificate, and the change an answer makes, for this computer's
signed-in window alone."""

from __future__ import annotations

import subprocess
import threading

from poolhouse import node_signing, runtime_identity
from poolhouse.log import say, warn

from . import session

UNFINISHED = "The change did not finish."


class Change:
    """The one keychain change under way in this process, and what the last one that did not finish said."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.error = ""

    @property
    def working(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, wanted: bool) -> bool:
        """Begin adding or removing the certificate, unless a change is under way; whether it began."""
        with self._lock:
            if self.working:
                return False
            self.error = ""
            self._thread = threading.Thread(target=self._run, args=(wanted,), name="signature-change")
            self._thread.start()
            return True

    def _run(self, wanted: bool) -> None:
        change = node_signing.enable if wanted else node_signing.remove
        signed: list = []
        self.error = UNFINISHED
        try:
            change(say, lambda: signed.extend(runtime_identity.resign_apps()), via="app window")
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
            self.error = str(error)[-600:]
            warn(f"certificate: {error}")
            return
        unsigned = wanted and any(how != "certificate" for _app, how in signed)
        self.error = "The certificate is there, but an app could not be signed with it. The next update tries again." if unsigned else ""


CHANGE = Change()


def state() -> dict:
    """What the window shows: nothing to choose where Poolhouse signs nothing."""
    if not runtime_identity.applies():
        return {"applies": False}
    certificate = bool(node_signing.usable_certificate())
    working = CHANGE.working
    return {"applies": True, "certificate": certificate, "answer": node_signing.answer(), "working": working, "error": CHANGE.error,
            "leftovers": not certificate and not working and node_signing.holds_leftovers()}


class SignatureRoutes:
    def route(self) -> bool:
        if self.path != "/ui/signature":
            return super().route()
        if not self._persons_window():
            self.ui.record("signature.refused", source=self.client_ip)
            self.send(403, {"error": "Answer this in this computer's signed-in Poolhouse window."})
            return True
        if self.method == "GET":
            self.send(200, state())
        elif self.method == "POST":
            self._answer()
        else:
            self.send(405, {"error": "use GET or POST"})
        return True

    def _persons_window(self) -> bool:
        """This computer's own window, in a session a person opened; a token, an agent's header or another machine is not one."""
        if not (self._local_browser(read=True) and session.person_session(self)):
            return False
        return self.ui.sessions.get(session.parse_cookie(self.cookie)).credentialed

    def _answer(self) -> None:
        try:
            size = int(self.header("Content-Length", "0") or 0)
        except ValueError:
            size = -1
        body = self.body() if 0 < size <= 256 else None
        if not isinstance(body, dict) or set(body) != {"certificate"} or body["certificate"] not in node_signing.ANSWERS:
            self.send(400, {"error": "answer yes or no"})
            return
        if not runtime_identity.applies():
            self.send(409, {"error": "Poolhouse signs no app on this computer, so there is nothing to choose."})
            return
        if CHANGE.working:
            self.send(409, {"error": "A change is under way. Answer the macOS prompt, if one is open."})
            return
        wanted = body["certificate"] == "yes"
        there = bool(node_signing.usable_certificate()) or (not wanted and node_signing.holds_leftovers())
        node_signing.remember(body["certificate"])
        self.ui.record("signature.answer", answer=body["certificate"], source=self.client_ip)
        if wanted != there:
            CHANGE.start(wanted)
        else:
            CHANGE.error = ""
        self.send(200, state())
