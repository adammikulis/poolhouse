"""``/ui/phones``: the computer's Phones screen. Showing a link code, listing and removing phones, switching a phone into the pool and
connecting projects are the person's, so each asks for a session of this computer whose sign-in the server checked: the app's own
window, or a command a person ran. A page open because no pool is founded yet is not enough, since any program here reaches it."""

from __future__ import annotations

import re

from poolhouse.phone_mailbox import PHONE_ID, Refused

ONE = re.compile(r"/ui/phones/(ph-[0-9a-f]{12})(/pool-use)?\Z")


class PhoneRoutes:
    def route(self) -> bool:
        if self.path != "/ui/phones" and not self.path.startswith("/ui/phones/"):
            return super().route()
        if (self.client_ip not in {"127.0.0.1", "::1"} or not self.ui.host_ok(self.host_header)
                or not self.ui.credentialed(self.cookie)):
            self.send(403, {"error": "Phones are managed on this computer, from the Poolhouse app's own window."})
            return True
        phones = self.ui.phones
        if phones is None:
            self.send(501, {"error": "This daemon was started without linked phones."})
            return True
        try:
            answered = self._phones(phones)
        except (Refused, OSError, RuntimeError, ValueError) as why:
            self.send(400, {"error": str(why)})
            return True
        if answered is None:
            self.send(404, {"error": "no such route"})
        else:
            self.send(200, answered, {"Cache-Control": "no-store"})
        return True

    def _phones(self, phones):
        path, method = self.path, self.method
        if path == "/ui/phones" and method == "GET":
            return phones.status()
        if path == "/ui/phones/offer" and method == "POST":
            return phones.offer()
        if path == "/ui/phones/offer" and method == "DELETE":
            phones.cancel_offer()
            return phones.status()
        if path == "/ui/phones/projects" and method == "POST":
            req = self.body()
            folder, on = req.get("path"), req.get("connected", True)
            if not isinstance(folder, str) or not folder.strip() or len(folder) > 2000 or not isinstance(on, bool):
                raise ValueError("Name a project folder and whether it is connected.")
            phones.connect(folder.strip(), on)
            return phones.status()
        found = ONE.match(path)
        if found is None or not PHONE_ID.match(found[1]):
            return None
        if method == "DELETE" and not found[2]:
            if not phones.remove(found[1]):
                raise ValueError("That phone is not linked.")
            return phones.status()
        if method == "POST" and found[2]:
            enabled = self.body().get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled is true or false.")
            phones.models.switch(found[1], enabled)
            return phones.status()
        return None
