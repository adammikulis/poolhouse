"""Studio APIs for workflows, runs and portable package operations."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from poolhouse import hub
from poolhouse.home import machine_id
from poolhouse.studio.adapters import Adapters
from poolhouse.studio.catalog import node_catalog
from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.decision_inventory import inventory as decision_inventory
from poolhouse.studio.engine import WorkflowEngine
from poolhouse.studio.library import LocalLibrary
from poolhouse.studio.packages import export_package, import_package
from poolhouse.studio.resources import model_resource
from poolhouse.studio.saved_models import PREFIX as SAVED_MODEL_PREFIX, SavedModels
from poolhouse.studio.store import Conflict, StudioStore, digest
from poolhouse.studio.templates import templates

from .discovery import load_pool_key
from .files import safe_relpath
from .managed_compute import process_environment
from .request_fields import field, object_body
from .session import parse_cookie
from .studio_artifacts import artifact_files, open_artifact, stream_artifact
from .studio_decision_setup import configure as configure_decision
from .studio_dispatch import DeviceDispatch
from .studio_library import provider as pool_library

_INITIALIZE = threading.Lock()


def _binding(ui) -> str:
    key = load_pool_key(ui.pool_key_path)
    return digest({"pool": hashlib.sha256(key).hexdigest() if key else "local",
                   "device": machine_id(), "board": str(Path(ui.root).resolve())})


class StudioRoutes:
    """Studio API routes called after the UI authorization guard."""

    def route(self) -> bool:
        if not self.path.startswith("/ui/studio/"):
            return super().route()
        try:
            return self._studio()
        except Conflict as exc:
            self.send(409, {"error": str(exc), "conflict_copy": exc.copy})
        except PermissionError as exc:
            self.send(403, {"error": str(exc)})
        except KeyError as exc:
            self.send(404, {"error": str(exc)})
        except (ValueError, OSError, RuntimeError) as exc:
            self.send(400, {"error": str(exc), "errors": exc.args[0] if exc.args and isinstance(exc.args[0], list) else []})
        return True

    def _service(self):
        ui = self.ui
        if ui.runner is None or not ui.root:
            raise ValueError("Start the daemon job queue to use Studio.")
        with _INITIALIZE:
            if not hasattr(ui, "studio"):
                store = StudioStore(Path(ui.root) / "studio.ladybug")
                ui._studio_principals = {}

                def authorize_resources(principal, node, inputs):
                    if principal.get("scope") != _binding(ui):
                        return False
                    checker = ui._studio_principals.get(principal.get("id"))
                    if checker is None or not checker():
                        return False
                    for binding in (node or {}).get("bindings", {}).values():
                        if not isinstance(binding, dict) or binding.get("kind") not in {"dataset", "model", "recordings", "traces"}:
                            return False
                        if binding["kind"] != "model":
                            try:
                                safe_relpath(ui.runner.files_root, binding.get("id", ""))
                            except (ValueError, RuntimeError):
                                return False
                    additional = getattr(ui, "studio_authorize", None)
                    return bool(additional(principal, node, inputs)) if additional else True

                saved = SavedModels(store, ui.runner.files_root, authorize_resources)

                def resolve_model(binding, context):
                    if binding.get('id', '').startswith(SAVED_MODEL_PREFIX):
                        return saved.resolve(context['principal'], binding['id'])
                    return model_resource(binding)

                def authorize(principal, node, inputs):
                    if not authorize_resources(principal, node, inputs):
                        return False
                    try:
                        saved.check_inputs(principal, inputs)
                        binding = (node or {}).get('bindings', {}).get('model', {})
                        if binding.get('id', '').startswith(SAVED_MODEL_PREFIX):
                            saved.resolve(principal, binding['id'])
                    except (ValueError, PermissionError, OSError, KeyError):
                        return False
                    return True

                ui.studio_saved_models = saved
                dispatcher = DeviceDispatch(ui, store, authorize)
                adapters = Adapters(ui.runner, process_environment=process_environment, model_resolver=resolve_model)
                ui.studio = WorkflowEngine(store, ui.runner, adapters, authorize, dispatcher)
        sid = parse_cookie(self.cookie)
        session = ui.sessions.get(sid)
        if sid and session is None:
            raise PermissionError("This session ended. Sign in again to use Studio.")
        if session:
            principal = {"id": digest({"session": sid}), "scope": _binding(ui), "owner": session.credentialed}
            ui._studio_principals[principal["id"]] = lambda: ui.sessions.get(sid) is not None
        else:
            principal = {"id": digest({"local": self.client_ip, "scope": _binding(ui)}), "scope": _binding(ui), "owner": True}
            ip, host, token = self.client_ip, self.host_header, self.header("X-Poolhouse-Setup", "")
            ui._studio_principals[principal["id"]] = lambda: not load_pool_key(ui.pool_key_path) and not ui.may_setup(ip, host, token)
        return ui.studio, principal

    def _studio(self) -> bool:
        if self.path == "/ui/studio/nodes" and self.method == "GET":
            nodes = node_catalog()
            if self.ui.runner is not None and self.ui.root:
                engine, _ = self._service()
                availability = getattr(engine.adapters, "availability", lambda nodes: {})(nodes)
                nodes = [{**node, "requirements": availability.get(node["type"], [])} for node in nodes]
            self.send(200, {"nodes": nodes})
            return True
        if self.path == "/ui/studio/templates" and self.method == "GET":
            self.send(200, {"templates": templates()})
            return True
        if self.path == "/ui/studio/validate" and self.method == "POST":
            checked = validate_graph(field(object_body(self), "graph", dict, {}))
            if checked["valid"]:
                engine, principal = self._service()
                remote = [node for node in checked["graph"]["nodes"] if engine._remote(node)]
                local = [node for node in checked["graph"]["nodes"] if node not in remote]
                checked["errors"].extend(engine.adapters.validate(local))
                checked["errors"].extend(engine.dispatcher.validate(remote, principal))
                checked["valid"] = not checked["errors"]
            self.send(200, checked)
            return True
        engine, principal = self._service()
        store, scope = engine.store, principal["scope"]
        if not engine.authorize(principal, None, {}):
            raise PermissionError("Sign in again to use Studio.")
        if self.path == "/ui/studio/devices" and self.method == "GET":
            self.send(200, {"devices": engine.dispatcher.devices()})
            return True
        if self.path == "/ui/studio/resources/decision/install" and self.method == "POST":
            status, payload = configure_decision(self, engine, principal)
            self.send(status, payload)
            return True
        if self.path == "/ui/studio/resources" and self.method == "GET":
            root = self.ui.runner.files_root
            datasets = [{"id": path.name, "title": path.name} for path in sorted(root.iterdir())
                        if path.name not in {"jobs", "studio", "studio-device-resources"} and not path.name.startswith(".")
                        and path.resolve().is_relative_to(root.resolve())] if root.exists() else []
            self.send(200, {"datasets": datasets, "models": [{"id": entry.id, "title": entry.id} for entry in hub.discover()]
                       + self.ui.studio_saved_models.list(principal) + decision_inventory()})
            return True
        if self.path.startswith("/ui/studio/workflows"):
            return self._workflows(store, scope)
        if self.path.startswith("/ui/studio/runs"):
            return self._runs(engine, principal)
        return self._packages(engine, principal)

    def _workflows(self, store, scope) -> bool:
        if self.path == "/ui/studio/workflows":
            if self.method == "GET":
                self.send(200, {"workflows": store.records("workflow", scope)})
            elif self.method == "POST":
                req = object_body(self)
                row = store.save_workflow(scope, field(req, "title", str, "Untitled workflow"), field(req, "graph", dict, {}))
                self.send(201, {"workflow": row})
            else:
                self.send(405, {"error": "Choose a supported workflow action."})
            return True
        if self.path.startswith("/ui/studio/workflows/"):
            ident = self.path.removeprefix("/ui/studio/workflows/")
            if self.method == "GET":
                self.send(200, {"workflow": store.get("workflow", ident, scope)})
            elif self.method in {"PUT", "POST"}:
                req = object_body(self)
                row = store.save_workflow(scope, field(req, "title", str, "Untitled workflow"), field(req, "graph", dict, {}),
                                          ident=ident, revision=field(req, "revision", int, 0))
                self.send(200, {"workflow": row})
            else:
                self.send(405, {"error": "Choose a supported workflow action."})
            return True
        return False

    def _runs(self, engine, principal) -> bool:
        store, scope = engine.store, principal["scope"]
        if self.path == "/ui/studio/runs":
            if self.method == "GET":
                self.send(200, {"runs": [self._public_run(row) for row in store.records("run", scope)
                                         if (principal.get("owner") or row["principal"]["id"] == principal["id"])
                                         and all(engine.authorize(principal, node, row["nodes"][node["id"]].get("outputs", {}))
                                                 for node in row["graph"]["nodes"])]})
            elif self.method == "POST":
                req = object_body(self)
                workflow = store.get("workflow", field(req, "workflow_id", str, ""), scope)
                if workflow["revision"] != field(req, "revision", int, 0):
                    raise Conflict("The workflow changed. Save your edits before running it.")
                outputs = field(req, "output_nodes", list, [])
                if any(not isinstance(value, str) for value in outputs):
                    raise ValueError("Selected outputs must be node identities.")
                run = engine.submit(principal, workflow, outputs, field(req, "idempotency_key", str, ""))
                self.send(202, {"run": self._public_run(run)})
            return True
        if self.path.startswith("/ui/studio/runs/"):
            parts = self.path.removeprefix("/ui/studio/runs/").split("/")
            run = store.get("run", parts[0], scope)
            if not principal.get("owner") and run["principal"]["id"] != principal["id"]:
                raise PermissionError("This run belongs to another session.")
            if any(not engine.authorize(principal, node, run["nodes"][node["id"]].get("outputs", {}))
                   for node in run["graph"]["nodes"]):
                raise PermissionError("A resource in this run is no longer authorized.")
            if len(parts) == 1 and self.method == "GET":
                self.send(200, {"run": self._public_run(store.get("run", parts[0], scope))})
            elif len(parts) == 2 and self.method == "POST" and parts[1] in {"cancel", "resume"}:
                row = getattr(engine, parts[1])(principal, parts[0])
                self.send(200, {"run": self._public_run(row)})
            elif len(parts) == 2 and parts[1] == "artifacts" and self.method == "GET":
                row = store.get("run", parts[0], scope)
                self.send(200, {"artifacts": [{"node": ident, "outputs": node["outputs"]}
                                              for ident, node in row["nodes"].items() if node["state"] == "done"],
                                "files": self._artifact_files(engine, principal, row)})
            elif parts[1:] == ["artifacts", "file"] and self.method in {"GET", "HEAD"}:
                selected = {name: self.asked(name) for name in ("node", "port", "name")}
                opened, filename = open_artifact(run, self.ui.runner.files_root, selected,
                                                lambda node, outputs: engine.authorize(principal, node, outputs))
                stream_artifact(self.handler, opened, filename)
            elif parts[1:] == ["logs"] and self.method == "GET":
                state = run["nodes"].get(self.asked("node"))
                if state is None:
                    raise KeyError("Choose a node in this run.")
                job = self.ui.runner.jobs.get(state.get("job_id"))
                if job is None:
                    self.send(200, {"log": "", "metrics": [], "state": state["state"]})
                else:
                    return self._job_detail(self.ui.runner, job)
            else:
                self.send(405, {"error": "Choose a supported run action."})
            return True
        return False

    def _artifact_files(self, engine, principal, run):
        def authorize(node, outputs):
            return engine.authorize(principal, node, outputs)
        result = artifact_files(run, self.ui.runner.files_root, authorize)
        for identifier, state in run["nodes"].items():
            if not state.get("reused_from"):
                continue
            original = self._artifact_origin(engine, principal, run, identifier)
            rows = artifact_files(original, self.ui.runner.files_root, authorize)
            result.extend({**row, "reused_from": original["id"]} for row in rows)
        return result

    @staticmethod
    def _artifact_origin(engine, principal, run, identifier):
        expected, visited = run["nodes"][identifier], {run["id"]}
        original = run
        while original["nodes"][identifier].get("reused_from"):
            source = original["nodes"][identifier]["reused_from"]
            if source in visited:
                raise ValueError("Artifact lineage contains a cycle.")
            visited.add(source)
            original = engine.store.get("run", source, principal["scope"])
            state = original["nodes"].get(identifier)
            if (not state or state.get("fingerprint") != expected.get("fingerprint")
                    or state.get("outputs") != expected.get("outputs")):
                raise ValueError("Cached artifact lineage does not match its source.")
            if not principal.get("owner") and original["principal"]["id"] != principal["id"]:
                raise PermissionError("The original artifact belongs to another session.")
            node = next(node for node in original["graph"]["nodes"] if node["id"] == identifier)
            if not engine.authorize(principal, node, state["outputs"]):
                raise PermissionError("The original artifact is no longer authorized.")
        return {**original, "graph": {**original["graph"], "nodes": [node for node in original["graph"]["nodes"]
                                                                   if node["id"] == identifier]},
                "nodes": {identifier: original["nodes"][identifier]}}

    @staticmethod
    def _public_run(run: dict) -> dict:
        return {key: value for key, value in run.items() if key not in {"principal", "scope"}}

    def _packages(self, engine, principal) -> bool:
        if not self.path.startswith("/ui/studio/packages"):
            self.send(404, {"error": "No such Studio route."})
            return True
        provider = self.asked("provider", "local")
        if provider == "pool":
            factory = getattr(self.ui, "studio_pool_library", None)
            library = factory(principal) if factory else pool_library(self.ui)
        elif provider != "local":
            raise ValueError("Choose the local or pool library.")
        else:
            library = LocalLibrary(engine.store.graph, scope=principal["scope"], lock=engine.store.lock)
        if self.path == "/ui/studio/packages" and self.method == "GET":
            self.send(200, {"packages": library.search(self.asked("q")),
                            "unavailable": getattr(library, "unavailable", [])})
        elif self.path == "/ui/studio/packages/export" and self.method == "POST":
            req = object_body(self)
            workflow = (engine.store.get("workflow", field(req, "workflow_id", str, ""), principal["scope"])
                        if req.get("workflow_id") else {"id": "selected", "title": "Selected nodes", "graph": field(req, "graph", dict, {})})
            package = export_package(workflow["graph"], package_id=field(req, "package_id", str, workflow["id"]),
                                     version=field(req, "version", str, "1.0.0"), name=field(req, "name", str, workflow["title"]),
                                     kind=field(req, "kind", str, "workflow"), licence=field(req, "licence", str, ""),
                                     description=field(req, "description", str, ""), tags=field(req, "tags", list, []),
                                     portable_parameters=field(req, "portable_parameters", dict, {}))
            self.send(200, {"package": package})
        elif self.path == "/ui/studio/packages/preview" and self.method == "POST":
            package = import_package(field(object_body(self), "package", dict, {}))
            self.send(200, {"package": package, "requirements": validate_graph(package["graph"])["errors"]})
        elif self.path == "/ui/studio/packages/import" and self.method == "POST":
            package = import_package(field(object_body(self), "package", dict, {}))
            library.publish(package)
            row = engine.store.save_workflow(principal["scope"], package["name"], package["graph"])
            self.send(201, {"package": package, "workflow": row})
        elif self.path == "/ui/studio/packages/publish" and self.method == "POST":
            package = import_package(field(object_body(self), "package", dict, {}))
            self.send(201, {"package": library.publish(package),
                            "unavailable": getattr(library, "unavailable", [])})
        else:
            self.send(405, {"error": "Choose a supported package action."})
        return True
