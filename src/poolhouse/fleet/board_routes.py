"""Local human controls for the board."""

import re

from .extension_routes import load
from .session import parse_cookie


class BoardRoutes:
    def route(self) -> bool:
        match = re.fullmatch(r"/ui/projects/([a-f0-9]{32})/(board/[a-z]+|tasks)", self.path)
        if match is None:
            return super().route()
        projects = getattr(self.ui, "projects", None)
        if projects is None:
            self.send(501, {"error": "project registry unavailable"})
            return True
        if (self.client_ip not in {"127.0.0.1", "::1"} or not self.ui.credentialed(self.cookie)
                or not self.ui.host_ok(self.host_header)):
            self.send(403, {"error": "board controls require a signed-in local person"})
            return True
        try:
            if self.method == "POST" and (
                    self.header("Origin") not in {f"http://{self.host_header}", f"https://{self.host_header}"}
                    or self.header("Sec-Fetch-Site", "same-origin") != "same-origin"):
                self.send(403, {"error": "board changes come from this page"})
                return True
            project = projects.get(match[1])
            if match[2] == "tasks":
                return load("tasks")(self, prefix=self.path, root=project.root,
                                         project={"key": match[1], "name": project.name})
            opened = self.ui.sessions.get(parse_cookie(self.cookie))
            return load("board")(self, root=project.root, prefix=f"/ui/projects/{match[1]}/board/",
                                      audit=lambda name: self.ui.record("person.connect", actor=name, project=match[1],
                                                                        origin=getattr(opened, "origin", "")))
        except (PermissionError, ValueError, TypeError, KeyError) as exc:
            self.send(409, {"error": str(exc)})
        return True
