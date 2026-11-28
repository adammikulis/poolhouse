"""Stray copies of Poolhouse, shown in this computer's own window, with the click that removes one (`poolhouse.devinstall`)."""

from poolhouse import devinstall

from . import session
from .initial_setup_routes import _loopback


class StrayCopyRoutes:
    def route(self):
        if self.path not in {"/ui/stray-copies", "/ui/stray-copies/remove"}:
            return super().route()
        removing = self.path.endswith("/remove")
        if removing and (self.method != "POST" or not self._local_browser() or not session.person_session(self)):
            self.send(403, {"error": "Remove a copy from this computer's signed-in window."})
            return True
        if not removing and (self.method != "GET" or not _loopback(self.client_ip) or not self._signed_in_window(read=True)):
            self.send(403, {"error": "Open this computer's own window to see its copies."})
            return True
        error = ""
        if removing:
            wanted = self.body()
            if not isinstance(wanted, dict) or set(wanted) != {"id"} or not isinstance(wanted["id"], str):
                self.send(400, {"error": "Name the one copy to remove."})
                return True
            error = devinstall.remove(wanted["id"])
        self.send(409 if error else 200, {"error": error, "copies": [
            {"id": one.id, "said": one.said, "command": " ".join(one.command)} for one in devinstall.copies()]})
        return True
