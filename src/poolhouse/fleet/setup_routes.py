"""First-run preferences, membership and preparation route ownership."""
from __future__ import annotations

from . import lan_pools, setup_project_routes
from .discovery import DiscoveryError, in_pool, require_name
from .onboard.joining import JoinOptions
from .onboard.pools import known_pools
from .session import parse_cookie


class SetupRoutes:
    """First run: what this machine looks like, what to set it to, and who may say so."""

    def _may_setup(self) -> str:
        return self.ui.may_setup(self.client_ip, self.host_header,
                                 self.header("X-Poolhouse-Setup", ""))

    def public_route(self) -> bool:
        if setup_project_routes.public(self):
            return True
        if self.path == "/ui/setup" and self.method == "GET":
            self.send(200, self.ui.state())
            return True
        if self.path == "/ui/setup/join" and self.method == "POST":
            return self._join()
        if self.path == "/ui/setup/pools" and self.method == "GET":
            why = self._may_setup() if not in_pool(self.ui.pool_key_path) else ""
            if why:
                self.send(403, {"error": why})
            elif in_pool(self.ui.pool_key_path) and not self.ui.authed(self.cookie):
                self.send(401, {"error": "sign in to discover pools"})
            else:
                try:
                    self.send(200, {"pools": lan_pools.nearby(port=self.ui.discovery_port),
                                    "found": [c.public() for c in known_pools(
                                        self.ui.pool_key_path, port=self.ui.discovery_port,
                                        self_names=(self.ui.name,))]})
                except OSError:
                    self.send(503, {"error": "LAN discovery is unavailable; enter the pool name manually."})
            return True
        if self.path == "/ui/setup/suggest" and self.method == "GET":
            return self._suggest()
        if self.path in ("/ui/setup/prefs", "/ui/setup/done") and self.method == "POST":
            return self._prefs()
        if self.path == "/ui/setup/members" and self.method == "GET":
            self.send(200, {"members": self.ui.members(force=True)})
            return True
        return super().public_route()

    def route(self) -> bool:
        if setup_project_routes.route(self):
            return True
        return super().route()

    def _join(self) -> bool:
        ui = self.ui
        joined = in_pool(ui.pool_key_path)
        if joined and not ui.authed(self.cookie):
            self.send(401, {"error": "this machine is already in a pool -- sign in to "
                                     "move it to another one"})
            return True
        if not joined:
            why = self._may_setup()
            if why:
                self.send(403, {"error": why})
                return True
        req = self.body()
        try:
            if not isinstance(req.get("existing", False), bool):
                raise DiscoveryError("existing pool selection must be true or false")
            held = ui.sessions.get(parse_cookie(self.cookie))
            state, sid = ui.join(str(req.get("passphrase") or ""),
                                 require_name(req.get("group")), self.client_ip,
                                 options=JoinOptions(action=req.get("mode"), existing=req.get("existing") is True,
                                                     mode=req.get("pool_mode")),
                                 origin=held.origin if held is not None and held.credentialed else "")
        except DiscoveryError as exc:
            self.send(429 if "attempts" in str(exc) or "busy" in str(exc) else 400,
                      {"error": str(exc)})
            return True
        session = ui.sessions.get(sid)
        self.send(200, state,
                  {"Set-Cookie": ui.sessions.cookie_header(session)} if session else None)
        return True

    def _suggest(self) -> bool:
        from .settings import suggest
        ui = self.ui
        report = ui.report() if callable(ui.report) else {}
        self.send(200, {
            "machine": {k: report.get(k) for k in
                        ("gpu", "vendor", "cpus", "ram_gb", "accelerator",
                         "vram_total_gb", "temp_c")},
            "suggest": {k: {"value": s.value, "why": s.why}
                        for k, s in suggest().items()},
            "current": ui.settings.public() if ui.settings else {},
        })
        return True

    def _prefs(self) -> bool:
        ui = self.ui
        if in_pool(ui.pool_key_path):
            if not ui.authed(self.cookie):
                self.send(401, {"error": "sign in first"})
                return True
        else:
            why = self._may_setup()
            if why:
                self.send(403, {"error": why})
                return True
        if self.path == "/ui/setup/done":
            self.send(200, ui.setup_finished())
            return True
        self.send(200, ui.apply_prefs(self.body()))
        return True

