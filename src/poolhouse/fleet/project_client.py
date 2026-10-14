"""Authenticated project discovery and source checkout."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from poolhouse import sealing
from poolhouse.fleet.remote import Discovery
from poolhouse.http import Options, Sealed, ServerError, ServerUnreachable, open_stream

from .discovery import derive_token, memberships
from .onboard.lan import require_local_url
from .project_source import MAX_ARCHIVE, MAX_FILES, MAX_SOURCE, ProjectError, checkout, project_id
from .remote import Member


def register_local(root, key, project_id, port):
    """Register a Git checkout with this device's sealed Fleet endpoint."""
    endpoint = f"http://127.0.0.1:{port}/workspace/v1/local-project"
    def destination(url):
        if url != endpoint:
            raise ProjectError("local project registration cannot redirect")
        return url
    try:
        with open_stream(endpoint, method="POST", data=json.dumps({"root": str(root.resolve()), "project_id": project_id}).encode(), options=Options(token=derive_token(key), headers={"Content-Type": "application/json", sealing.HEADER: "2"}, timeout=2, guard=destination)) as response:
            if not response.headers.get(sealing.HEADER):
                raise ProjectError("local project registration was not authenticated and sealed")
            limit = 65536 + sealing.NONCE_BYTES + 16
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ProjectError("local project registration exceeds its response limit")
            result = json.loads(response.sealed.open(response.status, response.headers, raw))
            if not isinstance(result, dict) or result.get("id") != project_id:
                raise ProjectError("local project registration returned another project")
    except ServerUnreachable as error:
        raise ProjectError("Local Fleet is unavailable; start poolhouse and retry project joining") from error
    except ServerError as error:
        raise ProjectError("local project registration failed") from error


def read(member: Member, path: str, limit: int, *, deadline: float | None = None) -> bytes:
    """Read one bounded signed and sealed project response."""
    endpoint = member.base_url + path

    def guard(url: str) -> str:
        if url != endpoint:
            raise ProjectError("Project source cannot redirect away from its authority endpoint")
        require_local_url(url)
        return url

    wire_limit = limit + sealing.NONCE_BYTES + 16
    timeout = min(30, member.timeout)
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise ProjectError("Project discovery exceeded its time limit")
    with open_stream(guard(endpoint), options=Options(token=member.token, guard=guard, headers={sealing.HEADER: "2"}, timeout=timeout)) as response:
        if not response.headers.get(sealing.HEADER):
            raise ProjectError("Project response was not authenticated and sealed")
        if deadline is None:
            data = response.read(wire_limit + 1)
        else:
            chunks, size = [], 0
            while size <= wire_limit:
                if time.monotonic() >= deadline:
                    raise ProjectError("Project discovery exceeded its time limit")
                chunk = response.read1(min(8192, wire_limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b"".join(chunks)
        if len(data) > wire_limit:
            raise ProjectError("Project response exceeds its size limit")
        opened = getattr(response, "sealed", Sealed()).open(response.status, response.headers, data)
        if len(opened) > limit:
            raise ProjectError("Project response exceeds its size limit")
        return opened


def catalogue(member: Member, *, deadline: float | None = None) -> dict:
    result = json.loads(read(member, "/workspace/v1/projects", 1 << 20, deadline=deadline))
    if (not isinstance(result, dict) or not isinstance(result.get("projects"), list)
            or not isinstance(result.get("machine"), str) or not 0 < len(result["machine"]) <= 256
            or len(result["projects"]) > 1000):
        raise ProjectError("Member returned an invalid project catalogue")
    for project in result["projects"]:
        validate_project(project)
        if project.get("source_machine") != result.get("machine"):
            raise ProjectError("Member advertised another device's project source")
    return result


def validate_project(project: dict) -> None:
    """Refuse malformed project metadata before selecting a source URL."""
    if not isinstance(project, dict):
        raise ProjectError("Member returned an invalid project entry")
    for field, length in (("id", 32), ("name", 256), ("source_machine", 256)):
        value = project.get(field)
        if not isinstance(value, str) or len(value) > length:
            raise ProjectError(f"Invalid project {field}")
    project_id(project["id"])
    for field in ("source_hash", "archive_sha256"):
        value = project.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ProjectError(f"Invalid project {field}")
    for field, maximum in (("size_bytes", MAX_SOURCE), ("files", MAX_FILES)):
        value = project.get(field)
        if type(value) is not int or not 0 <= value <= maximum:
            raise ProjectError(f"Invalid project {field}")


def members(ui) -> list[Member]:
    found = {}
    for member in memberships(ui.pool_key_path):
        for daemon in Member.discover(discovery=Discovery(pool_key_path=ui.pool_key_path, key=member.key, group=member.group, timeout_s=1, port=ui.discovery_port)):
            found.setdefault(daemon.base_url, daemon)
    return list(found.values())


def available(ui) -> dict:
    projects, devices = [], []
    if ui.projects:
        projects = [{**p, "is_self": True, "member": "", "state": "available"}
                    for p in ui.projects.list()]
    for member in members(ui):
        if member.beacon and ui.projects and member.beacon.machine == ui.projects.machine:
            continue
        try:
            found = catalogue(member)
            projects.extend({**p, "is_self": False, "member": member.base_url,
                             "host": member.base_url, "state": "available"} for p in found["projects"])
            devices.append({"name": member.name, "member": member.base_url, "state": "available"})
        except (ServerError, ProjectError, ValueError, OSError) as exc:
            devices.append({"name": member.name, "member": member.base_url,
                            "state": "upgrade_required" if getattr(exc, "status", 0) in {404, 501} else "offline",
                            "error": str(exc)})
    workspaces = [{**board, "is_self": True} for board in ui.projects.boards()] if ui.projects else []
    return {"projects": projects, "devices": devices, "workspaces": workspaces,
            "candidates": ui.projects.candidates() if ui.projects else [],
            "local": ui.projects.list() if ui.projects else []}


def receive(member: Member, identifier: str, base: Path) -> Path:
    rows = [p for p in catalogue(member)["projects"] if p["id"] == project_id(identifier)]
    if len(rows) != 1:
        raise ProjectError("Project is not published by this device")
    project = rows[0]
    data = read(member, f"/workspace/v1/projects/{identifier}/snapshot?hash={project['source_hash']}", MAX_ARCHIVE)
    if hashlib.sha256(data).hexdigest() != project["archive_sha256"]:
        raise ProjectError("Project archive digest mismatch")
    return checkout(data, identifier, project["source_hash"], base,
                    origin={"source_machine": project["source_machine"], "source_host": member.base_url})


def route(request) -> bool:
    ui, path, method = request.ui, request.path, request.method
    if path != "/ui/projects" and not path.startswith("/ui/projects/"):
        return False
    if not ui.projects:
        request.send(501, {"error": "project registry unavailable"})
        return True
    suffix = path.removeprefix("/ui/projects").strip("/").split("/")
    try:
        if path == "/ui/projects" and method == "GET":
            request.send(200, available(ui))
        elif path == "/ui/projects" and method == "POST":
            body = request.body()
            if body.get("action") != "share":
                raise ProjectError("Choose share explicitly")
            request.send(200, {"project": ui.projects.share(body.get("candidate_id", ""), body.get("name", "")).public()})
        elif len(suffix) == 1 and method == "PATCH":
            if request.body().get("shared") is not False:
                raise ProjectError("Only explicit unsharing is accepted")
            ui.projects.unshare(suffix[0])
            request.send(200, {"ok": True})
        elif len(suffix) == 2 and suffix[1] == "checkout" and method == "POST":
            selected = request.body().get("member")
            selected_members = [p for p in members(ui) if p.base_url == selected]
            if len(selected_members) != 1:
                raise ProjectError("Choose a currently authenticated pool device")
            result = receive(selected_members[0], suffix[0], ui.root / "project-checkouts")
            request.send(200, {"project_id": suffix[0], "checkout": str(result), "attached": False})
        else:
            return False
    except (ProjectError, ServerError, ValueError, OSError) as exc:
        request.send(400, {"error": str(exc)})
    return True
