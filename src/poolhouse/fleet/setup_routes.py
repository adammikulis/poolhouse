"""First-run preferences and preparation route ownership."""
from __future__ import annotations

from . import setup_preparation_routes, setup_project_routes
from .request_fields import object_body
from .settings import apply_preferences


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
        if self.path == "/ui/setup/suggest" and self.method == "GET":
            return self._suggest()
        if self.path in ("/ui/setup/prefs", "/ui/setup/done") and self.method == "POST":
            return self._prefs()
        if self.path == "/ui/setup/members" and self.method == "GET":
            self.send(200, {"members": self.ui.members(force=True)})
            return True
        return super().public_route()

    def route(self) -> bool:
        if setup_preparation_routes.route(self):
            return True
        if setup_project_routes.route(self):
            return True
        return super().route()

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
        if ui.founded():
            if not ui.authed(self.cookie):
                self.send(401, {"error": "sign in first"})
                return True
        else:
            why = self._may_setup()
            if why:
                self.send(403, {"error": why})
                return True
        if self.path == "/ui/setup/done":
            try:
                request = object_body(self)
                if set(request) - {"system_theme"}:
                    raise ValueError("Unknown setup completion field")
            except ValueError:
                self.send(400, {"error":"Setup completion needs an optional light or dark system theme."})
                return True
            if "system_theme" in request:
                if ui.settings is None:
                    self.send(503, {"error":"Preferences are unavailable on this device."})
                    return True
                if error := apply_preferences(ui.settings, {"system_theme":request["system_theme"]}):
                    self.send(400, {"error":error})
                    return True
            self.send(200, ui.setup_finished())
            return True
        self.send(200, ui.apply_prefs(self.body()))
        return True
