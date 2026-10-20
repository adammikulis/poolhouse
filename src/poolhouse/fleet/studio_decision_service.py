"""Daemon-owned stable decision assets; each lookup retains current authority."""
from __future__ import annotations

import copy
import secrets
import threading
from dataclasses import asdict
from pathlib import Path

from poolhouse import home, maintenance
from poolhouse.serve.decision_launch import BrokerBusy, OwnedBroker
from poolhouse.serve.decision_residents import DecisionModel, fingerprint, peak_estimate
from poolhouse.studio.decision_models import source
from poolhouse.studio.decisions import assess, canonical_record
from poolhouse.studio.engine import AdmissionWaiting, artifact_fingerprints

from .discovery import key_path
from .studio_decision_issuer import DecisionIssuer


class StableDecisionModels:
    """Sanitize once per verified content/runtime; never materialize during inference."""

    def __init__(self, engine):
        self.engine = engine
        self.root = engine.runner.files_root / 'studio-decision-runtime'
        self.lock = threading.RLock()
        self.models = {}

    def __call__(self, node, inputs, context):
        initial = context.get('decision_materialize') is True
        with maintenance.read(existing=not initial), self.lock:
            if self.engine.authorize(context['principal'], node, inputs) is not True:
                raise PermissionError('The decision model is no longer authorized.')
            model = inputs['model']
            _, identity = source(model, None)
            dependency = self.engine.adapters.dependency_fingerprint(node)
            fixed = {'identity': identity, 'artifacts': artifact_fingerprints(model),
                     'runtime': self.engine.implementation, 'dependency': dependency}
            key = fingerprint(fixed)
            cached = self.models.get(key)
            if cached is None:
                if not initial:
                    raise PermissionError('This decision model has no current owned materialization.')
                directory = self.root / key
                current, checked = source(model, directory)
                if checked != identity:
                    raise PermissionError('The decision model changed during materialization.')
                canonical = {name: str(value) if isinstance(value, Path) else value for name, value in asdict(current).items()}
                owned = {'kind': 'model', 'path': str(directory)}
                cached = {'model': DecisionModel(canonical, fingerprint(fixed), peak_estimate(canonical)),
                          'owned': owned, 'artifacts': artifact_fingerprints(owned)}
                self.models[key] = cached
            if cached['artifacts'] != artifact_fingerprints(cached['owned']):
                raise PermissionError('The owned decision materialization changed.')
            _, checked = source(model, None)
            if checked != identity or self.engine.authorize(context['principal'], node, inputs) is not True:
                raise PermissionError('The decision model authority or content changed.')
            return copy.deepcopy(cached['model'])


def create(ui, engine):
    """Install a lazy resident issuer; creating the application starts no broker."""
    service = ResidentDecisions(ui, engine)
    engine.runner.studio_decision_mint = service.mint
    engine.runner.studio_decision_preflight = service.preflight
    return service


class ResidentDecisions:
    """Actual daemon identity and retained child proofs are the only issuer bootstrap."""

    def __init__(self, ui, engine):
        self.ui, self.engine = ui, engine
        self.models = StableDecisionModels(engine)
        self.issuer = None
        self.bootstrap = None
        self.lock = threading.RLock()

    def _identity(self):
        context = self.ui.studio_authority._context()
        identity = self.ui.studio_identity()
        endpoint = getattr(self.ui, 'studio_endpoint', lambda: None)()
        if not identity or identity.fingerprint != context['device'] or not endpoint:
            raise PermissionError('The current pool daemon requires its existing secure listener for live decisions.')
        machine = home.state('machine-id').read_text().strip()
        if not machine or key_path(self.ui.pool_key_path) != home.state('pool.key'):
            raise PermissionError('Resident decisions require the current enrolled device profile.')
        return {'endpoint': endpoint, 'certificate': identity.beacon, 'machine': machine,
                'pool': context['pool'], 'runtime': self.engine.implementation}

    def _issuer(self):
        current = self._identity()
        with self.lock:
            if self.issuer is not None:
                if current != self.bootstrap:
                    raise PermissionError('The resident issuer identity changed; pending work needs reconciliation.')
                return self.issuer
            owned = OwnedBroker({**current, 'issuer_instance': secrets.token_hex(32)})
            issuer = DecisionIssuer(self.engine, owned, runtime=self.engine.implementation,
                                    resolve_model=self.models, worker_proof=self.engine.runner.verify_studio_worker,
                                    authorize=self.engine.authorize)
            self.bootstrap, self.issuer = current, issuer
            self.ui.studio_decision_issuer = issuer
            self.engine.runner.studio_launch_context = issuer.launch_context
            return issuer

    def mint(self, run_id, node_id, job_id):
        with maintenance.read():
            issuer = self._issuer()
            try:
                issuer.owned.start()
            except BrokerBusy as exc:
                raise AdmissionWaiting(str(exc)) from exc
            return issuer.mint(run_id, node_id, job_id)

    def preflight(self, node, inputs, context):
        if node['type'] != 'guard.injection':
            return None
        if self.engine.authorize(context['principal'], node, inputs) is not True:
            raise PermissionError('The guard resource is no longer authorized.')
        try:
            canonical_record(node, inputs)
        except ValueError:
            with maintenance.read():
                _, identity = source(inputs['model'], None)
                return assess(node['type'], node.get('parameters', {}), inputs['record'],
                              lambda *_args: (_ for _ in ()).throw(ValueError('Assessment input is unavailable.')),
                              identity=identity)
        return None

    def close(self):
        if self.issuer:
            self.issuer.close()
