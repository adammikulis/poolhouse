"""Create the shared Studio engine independently of a browser request."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from poolhouse.home import machine_id
from poolhouse.studio.adapters import Adapters
from poolhouse.studio.engine import WorkflowEngine
from poolhouse.studio.resources import model_resource
from poolhouse.studio.saved_models import PREFIX as SAVED_MODEL_PREFIX, SavedModels
from poolhouse.studio.store import StudioStore, digest

from .discovery import load_pool_key
from .files import safe_relpath
from .managed_compute import process_environment
from .studio_decision_service import create as create_decisions
from .studio_dispatch import DeviceDispatch
from .studio_stream_service import create as create_streams

_INITIALIZE = threading.RLock()


def scope(ui) -> str:
    key = load_pool_key(ui.pool_key_path)
    return digest({"pool": hashlib.sha256(key).hexdigest() if key else "local",
                   "device": machine_id(), "board": str(Path(ui.root).resolve())})


def initialize(ui):
    with _INITIALIZE:
        if hasattr(ui, 'studio'):
            return ui.studio
        return _create(ui)


def _create(ui):
    store = StudioStore(Path(ui.root) / "studio.ladybug")
    ui._studio_principals = {}
    ui._studio_origins = {}

    def authorize_resources(principal, node, inputs):
        if principal.get("kind") == "studio_subscription":
            authority = getattr(ui, "studio_authority", None)
            return authority.authorize(principal, node, inputs) if authority else False
        if principal.get("scope") != scope(ui):
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
            principal = context['principal']
            if principal.get('kind') == 'studio_subscription':
                grant = ui.studio_authority.get(principal)
                proof = next((p for p in grant['manifest']['resources'] if p.get('id') == binding['id']), None)
                if proof is None:
                    raise PermissionError('This saved model is outside the execution grant.')
                return saved.resolve_granted(principal, proof, authorize=lambda caller, pin, node, values:
                    pin in ui.studio_authority.get(caller)['manifest']['resources'] and
                    (getattr(ui, 'studio_authorize', lambda *args: True)(caller, node, values) is True))
            return saved.resolve(principal, binding['id'])
        return model_resource(binding)

    def authorize(principal, node, inputs):
        if not authorize_resources(principal, node, inputs):
            return False
        try:
            if principal.get('kind') == 'studio_subscription':
                for value in inputs.values():
                    if (isinstance(value, dict) and 'studio_reference' in value
                            and resolve_model({'kind': 'model', 'id': value['studio_reference']}, {'principal': principal}) != value):
                        return False
                return True
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
    ui.runner.studio_model_completed = saved.register
    ui.runner.studio_initializing = True
    ui.studio = WorkflowEngine(store, ui.runner, adapters, authorize, dispatcher)
    streams = create_streams(ui, ui.studio, saved, scope)
    decisions = create_decisions(ui, ui.studio)
    ui.studio.on_close = lambda: (decisions.close(), streams.webhook_setup.close(), streams.close())
    ui.studio.on_closed = ui.studio_authority.close
    ui.runner.studio_initializing = False
    streams.start_scheduler()
    return ui.studio
