"""Local human controls for the board."""

import re
import shlex

from .discovery import memberships
from .extension_routes import load
from .projects import lan_host
from .session import parse_cookie


class BoardRoutes:
    def route(self) -> bool:
        match = re.fullmatch(r"/ui/projects/([a-f0-9]{32})/(board(?:/[a-z]+)?|tasks|invite|adopt)", self.path)
        if match is None:
            return super().route()
        projects = getattr(self.ui, "projects", None)
        host = getattr(self.ui, "workspaces", None)
        if projects is None or host is None:
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
            if match[2] == "tasks":
                project = projects.get(match[1])
                return load("tasks")(self, prefix=self.path, root=project.root,
                                         project={"key": match[1], "name": project.name})
            if match[2].startswith("board/"):
                project = projects.get(match[1])
                opened = self.ui.sessions.get(parse_cookie(self.cookie))
                return load("board")(self, root=project.root, prefix=f"/ui/projects/{match[1]}/board/",
                                          audit=lambda name: self.ui.record("person.connect", actor=name, project=match[1],
                                                                            origin=getattr(opened, "origin", "")))
            if match[2] == "board" and self.method == "GET":
                self.send(200, host.status(match[1]))
            elif match[2] == "invite" and self.method == "POST":
                project = projects.get(match[1])
                if project.board_host and not projects.hosts(project.board_host):
                    raise ValueError("Create agent access on the project's authority device")
                network_host = lan_host(self.ui.member_port)
                if not network_host:
                    raise ValueError("No reachable network address is available for sharing this Board")
                req = self.body()
                made = host.invite(match[1], str(req.get("hint") or ""), int(req.get("uses") or 1))
                made["host"] = network_host
                groups = [member.group for member in memberships(self.ui.pool_key_path)]
                selected_pool = groups[0] if len(groups) == 1 else "POOL"
                made["command"] = (f"poolhouse-workspace remote join {made['code']} --name NAME "
                                   f"--model MODEL --harness HARNESS --host {shlex.quote(made['host'])} "
                                   f"--project-id {match[1]} --pool {shlex.quote(selected_pool)}")
                self.send(201, made)
            elif match[2] == "adopt" and self.method == "POST":
                self.send(200, host.adopt(match[1], self.body()))
            else:
                self.send(405, {"error": "read board status or create an agent invitation"})
        except (PermissionError, ValueError, TypeError, KeyError) as exc:
            self.send(409, {"error": str(exc)})
        return True
