"""Instance-bound resident permits reconstructed from the daemon's current run state."""
from __future__ import annotations

import copy
import hashlib
import re
import secrets
import threading
from dataclasses import asdict

from poolhouse import maintenance
from poolhouse.serve.decision_launch import OwnedBroker
from poolhouse.serve.decision_residents import DecisionModel, fingerprint
from poolhouse.studio.decisions import canonical_record
from poolhouse.studio.store import Conflict, digest


class DecisionIssuer:
    """Only retained daemon state and private launch proofs authorize resident work.

    Permits live for this issuer instance only. A daemon restart cannot restore them
    from a request file or remint an already launched job.
    """

    def __init__(self, engine, owned_broker: OwnedBroker, *, runtime, resolve_model, worker_proof, authorize):  # noqa: PLR0913 - independent authority callbacks remain explicit
        if (not isinstance(owned_broker, OwnedBroker) or not isinstance(runtime, str)
                or not re.fullmatch('[a-f0-9]{64}', runtime)
                or runtime != owned_broker.issuer['runtime'] or runtime != engine.implementation
                or not all(callable(callback) for callback in (resolve_model, worker_proof, authorize))):
            raise ValueError('The decision issuer requires its current owned runtime and trusted callbacks.')
        self.engine, self.owned = engine, owned_broker
        self.runtime = runtime
        self.resolve_model, self.worker_proof, self.current_authorize = resolve_model, worker_proof, authorize
        self.lock = threading.RLock()
        self.permits, self.jobs = {}, {}
        self.closed = False

    def _snapshot(self, run_id, node_id, job_id):
        if self.closed:
            raise PermissionError('The decision issuer instance ended.')
        engine = self.engine
        with engine.lock, engine.store.lock:
            run = copy.deepcopy(engine.store.graph.get_doc('studio.run:' + run_id))
            if not run or run['state'] != 'running':
                raise PermissionError('The decision run is no longer active.')
            node = next((item for item in run['graph']['nodes'] if item['id'] == node_id), None)
            state = run['nodes'].get(node_id, {})
            if (node is None or node['type'] not in {'decision.predict', 'guard.injection'}
                    or state.get('state') != 'running' or state.get('job_id') != job_id
                    or job_id != 'studio-' + digest({'run': run_id, 'node': node_id})[:40]
                    or engine.implementation != self.runtime):
                raise PermissionError('The decision job no longer matches its immutable run intent.')
            edges = [edge for edge in run['graph']['edges'] if edge['target']['node'] == node_id]
            inputs = {}
            for edge in edges:
                predecessor = run['nodes'][edge['source']['node']]
                if predecessor['state'] != 'done':
                    raise PermissionError('A decision input is no longer complete.')
                inputs[edge['target']['port']] = predecessor['outputs'][edge['source']['port']]
            context = {'run_id': run_id, 'node_id': node_id, 'files_root': engine.runner.files_root,
                       'output_dir': engine.runner.files_root / 'studio' / run_id / hashlib.sha256(node_id.encode()).hexdigest()[:24],
                       'bindings': node.get('bindings', {}), 'principal': run['principal']}
            # This callback must inspect current session/service authority, never an
            # owner flag copied into a stored principal.
            if self.current_authorize(run['principal'], node, inputs) is not True:
                raise PermissionError('Current decision authority is unavailable.')
            pending, seen = [node_id], set()
            while pending:
                ident = pending.pop()
                if ident in seen:
                    continue
                seen.add(ident)
                ancestor = next(item for item in run['graph']['nodes'] if item['id'] == ident)
                ancestor_inputs = {}
                for edge in run['graph']['edges']:
                    if edge['target']['node'] != ident:
                        continue
                    predecessor = run['nodes'][edge['source']['node']]
                    if predecessor['state'] != 'done':
                        raise PermissionError('A decision dependency is no longer complete.')
                    ancestor_inputs[edge['target']['port']] = predecessor['outputs'][edge['source']['port']]
                    pending.append(edge['source']['node'])
                event_input = run.get('event', {}).get('input_node')
                if ancestor['type'] == 'stream.record' and ident in run['graph'].get('variants', {}).get(event_input, [event_input]):
                    ancestor_inputs['record'] = run['event']['payload']
                if self.current_authorize(run['principal'], ancestor, ancestor_inputs) is not True:
                    raise PermissionError('Current decision dependency authority is unavailable.')
                for control in run['graph'].get('controls', []):
                    if control['target']['node'] != ident:
                        continue
                    source = control['source']
                    predecessor = run['nodes'][source['node']]
                    gates = {name: predecessor.get('outputs', {}).get(name) for name in ('allow', 'review', 'block')}
                    if (predecessor['state'] != 'done' or any(
                            not isinstance(gate, dict) or set(gate) != {'allow', 'reason'}
                            or type(gate['allow']) is not bool or gate['reason'] != source['port']
                            for gate in gates.values())
                            or [name for name, gate in gates.items() if gate['allow']] != [source['port']]):
                        raise PermissionError('A current decision control does not allow execution.')
                    pending.append(source['node'])
            record = canonical_record(node, inputs)
            fixed = {'run': run_id, 'node': node_id, 'job': job_id, 'graph': run['graph'],
                     'principal': run['principal'], 'inputs': inputs, 'record': record, 'runtime': self.runtime}
        return fixed, node, inputs, context

    def _model(self, node, inputs, context):
        model = self.resolve_model(node, inputs, context)
        if not isinstance(model, DecisionModel) or type(model.memory_bytes) is not int or model.memory_bytes <= 0:
            raise PermissionError('The authorized decision model is unavailable.')
        value = asdict(model)
        fingerprint(value)
        return value

    def mint(self, run_id, node_id, job_id):
        """Called only after the engine persists the exact running job intent."""
        fixed, node, inputs, context = self._snapshot(run_id, node_id, job_id)
        model = self._model(node, inputs, {**context, 'decision_materialize': True})
        if fingerprint(self._snapshot(run_id, node_id, job_id)[0]) != fingerprint(fixed):
            raise PermissionError('The decision intent changed during model resolution.')
        fixed_digest = fingerprint(fixed)
        record_digest = fingerprint(fixed['record'])
        model_digest = fingerprint(model)
        request_id = digest({'run': run_id, 'node': node_id, 'job': job_id, 'record': record_digest})
        with self.lock:
            previous = self.jobs.get(job_id)
            if previous:
                permit = self.permits[previous]
                if permit['fixed_digest'] != fixed_digest or permit['model_digest'] != model_digest:
                    raise Conflict('This decision job already names another immutable request.')
                return copy.deepcopy(permit['request'])
            if job_id in self.engine.runner.jobs:
                raise PermissionError('An existing decision job requires reconciliation, not a new permit.')
            permit_id = secrets.token_hex(32)
            request = {'permit_id': permit_id, 'request_id': request_id,
                       'model_digest': model_digest, 'record': fixed['record']}
            permit = {'run_id': run_id, 'node_id': node_id, 'job_id': job_id, 'request': request,
                      'fixed_digest': fixed_digest, 'model_digest': model_digest,
                      'record_digest': record_digest, 'model': model,
                      'request_digest': fingerprint({'node': node, 'inputs': inputs,
                                                     'output_dir': str(context['output_dir'])})}
            self.permits[permit_id], self.jobs[job_id] = permit, permit_id
            return copy.deepcopy(request)

    def _current(self, permit):
        with maintenance.read(existing=True):
            fixed, node, inputs, context = self._snapshot(permit['run_id'], permit['node_id'], permit['job_id'])
            if fingerprint(fixed) != permit['fixed_digest']:
                raise PermissionError('The decision request snapshot changed.')
            model = self._model(node, inputs, context)
            if fingerprint(model) != permit['model_digest']:
                raise PermissionError('The authorized decision model content changed.')
            if fingerprint(self._snapshot(permit['run_id'], permit['node_id'], permit['job_id'])[0]) != permit['fixed_digest']:
                raise PermissionError('The decision intent changed during model resolution.')
            return model

    def close(self):
        """Invalidate this private authority; broker retirement remains coordinated."""
        with self.lock:
            self.closed = True
            self.permits.clear()
            self.jobs.clear()

    def launch_context(self, job):
        """Private runner hook; the launch channel carries this, never job metadata."""
        with self.lock:
            permit = copy.deepcopy(self.permits.get(self.jobs.get(job.id)))
        if permit is None:
            return None
        self._current(permit)
        # Starting or reconciling a broker can wait; hold no issuer/engine/store lock.
        broker = self.owned.start()
        model = self._current(permit)
        return {'permit_id': permit['request']['permit_id'], 'request_id': permit['request']['request_id'],
                'run_id': permit['run_id'], 'node_id': permit['node_id'],
                'request_digest': permit['request_digest'], 'model_digest': permit['model_digest'],
                'calibration_digest': digest([model['source']['temperature'], model['source']['details']]), 'broker': broker}

    def authorize(self, payload, remote, local):
        """The sealed member route supplies actual accepted socket endpoints."""
        body = self.owned.verify(payload, remote, local)
        with self.lock:
            permit = copy.deepcopy(self.permits.get(body['permit_id']))
        if permit is None:
            raise PermissionError('This daemon instance did not mint the decision permit.')
        request = permit['request']
        if (body['request_id'] != request['request_id'] or body['record_digest'] != permit['record_digest']
                or body['model_digest'] != permit['model_digest'] or body['runtime_digest'] != self.runtime):
            raise PermissionError('The decision proof names a different immutable request.')
        model = self._current(permit)
        worker = body['worker']
        if self.worker_proof(permit['job_id'], worker['pid'], worker['started'],
                             request['permit_id'], request['request_id']) is not True:
            raise PermissionError('The decision worker has no current private launch proof.')
        # Proof verification can observe a changing grant; recheck after it too.
        self._current(permit)
        return {'authorized': True, 'permit_id': request['permit_id'], 'request_id': request['request_id'],
                'record_digest': permit['record_digest'], 'model_digest': permit['model_digest'],
                'runtime': self.runtime, 'stage': body['stage'], 'model': model}
