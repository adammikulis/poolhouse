"""Daemon-owned stable decision assets; each lookup retains current authority."""
from __future__ import annotations

import copy
import threading
from dataclasses import asdict
from pathlib import Path

from poolhouse import maintenance
from poolhouse.serve.decision_residents import DecisionModel, fingerprint, peak_estimate
from poolhouse.studio.decision_models import source
from poolhouse.studio.engine import artifact_fingerprints


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
