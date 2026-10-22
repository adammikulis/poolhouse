"""Broker decision residency without a real model, broker or machine state."""

import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from poolhouse import leasetable
from poolhouse.serve.decision_residents import (
    DecisionModel,
    DecisionOwner,
    DecisionResidents,
    fingerprint,
    peak_estimate,
)
from poolhouse.serve.process import started_at

pytest_plugins = ['node_kit']

RECORD = {"question": "Is this allowed?", "state": "A harmless record", "options": {"yes": "Allowed", "no": "Refused"}}


class Ledger:
    def __init__(self):
        self.docs = {}

    def get_doc(self, key):
        return self.docs.get(key)

    def put_doc(self, key, value):
        self.docs[key] = dict(value)

    def close(self):
        pass


class Table:
    def __init__(self):
        self.held = set()
        self.admit = True

    def budget(self, _value):
        pass

    def acquire(self, resources, terms):
        assert resources[0]["type"] == "memory_mb"
        if not self.admit:
            return {"state": "busy"}
        identity = str(len(self.held))
        self.held.add(identity)
        return {"state": "held", "id": identity}

    def renew(self, identity, **_terms):
        assert identity in self.held

    def release(self, identity):
        self.held.remove(identity)


class Process:
    def __init__(self, table):
        assert table.held
        self.pid = os.getpid()
        self.ended = False
        self.commands = []
        self.measured = 100

    def exchange(self, command, connected):
        assert connected()
        self.commands.append(command["op"])
        if command["op"] == "load":
            return {"ready": True, "memory": {"rss": self.measured, "device": self.measured}}
        return {"result": {"choice": "yes", "scores": {"yes": .8, "no": .2}},
                "memory": {"rss": self.measured, "device": self.measured}}

    def memory(self):
        return self.measured

    def alive(self):
        return not self.ended

    def close(self):
        self.ended = True


@pytest.fixture
def kit(tmp_path, monkeypatch):
    import json

    (tmp_path / "config.json").write_text(json.dumps({"model_type": "qwen3_5_text", "hidden_size": 8,
                                                   "num_hidden_layers": 1, "num_attention_heads": 1}))
    (tmp_path / "weights.safetensors").write_bytes(b"fake weights")
    source = {"name": "fixture", "base_dir": str(tmp_path), "tokenizer_dir": str(tmp_path),
              "head": str(tmp_path / "weights.safetensors"), "lora_dir": None, "dtype": "bfloat16",
              "pointer_dim": 2, "temperature": 1.0}
    model = DecisionModel(source, "verified-source", peak_estimate(source))
    table, processes, claims, active = Table(), [], [], [False]

    def backend():
        process = Process(table)
        processes.append(process)
        return process

    def claim(_name, pid, info, **options):
        if options.get("connected") and not options["connected"]():
            raise InterruptedError("request revoked")
        claims.append(info["purpose"])
        assert not active[0]
        active[0] = True
        return {"granted": True}

    def unclaim(*_args):
        active[0] = False

    monkeypatch.setattr("poolhouse.serve.decision_residents.admission.budget_mb", lambda: 4096)
    broker = SimpleNamespace(table=table, claim=claim, unclaim=unclaim, idle_s=600)
    ledger = Ledger()
    residents = DecisionResidents(broker, backend=backend, ledger=ledger)
    owner = DecisionOwner("fixture-session", os.getpid(), started_at(os.getpid()))
    try:
        yield SimpleNamespace(residents=residents, broker=broker, model=model, owner=owner,
                              processes=processes, table=table, ledger=ledger, claims=claims, active=active)
    finally:
        residents.close()


def call(kit, identity="first", **options):
    return kit.residents.request(RECORD, identity, kit.owner, options.get("resolve", lambda _stage: kit.model),
                                 options.get("connected", lambda: True))


def test_warm_records_reuse_host_but_take_compute_each_time(kit):
    first = call(kit)
    assert call(kit, "second") == first
    assert len(kit.processes) == 1
    assert kit.processes[0].commands == ["load", "decide", "decide"]
    assert len(kit.claims) == 3
    assert kit.residents.snapshot()[0]["reserved_bytes"] == kit.model.memory_bytes
    assert kit.table.held


def test_full_model_identities_get_distinct_native_contract_sized_memory_tags(kit, workspace_node):
    """The actual native parser admits production tags; all model work stays fake."""
    native = leasetable.Table(workspace_node.state)
    acquire = kit.table.acquire
    leases = {}
    def admitted(resources, terms):
        held = native.acquire([leasetable.memory(1)], terms)
        assert held['state'] == 'held'
        leases[terms.tag] = held['id']
        assert native.acquire([leasetable.memory(1)], terms)['id'] == held['id']
        return acquire(resources, terms)
    kit.table.acquire = admitted
    try:
        for suffix in ('0', '1'):
            kit.model = replace(kit.model, identity='a' * 63 + suffix)
            call(kit, 'request-' + suffix)
        identities = set(kit.residents.residents)
        assert len(identities) == 2 and all(len(identity) == 64 for identity in identities)
        assert set(leases) == {fingerprint({'lease': 'decision.resident', 'model': identity})
                               for identity in identities}
        assert all(len(tag) == 64 and set(tag) <= set('0123456789abcdef') for tag in leases)
        assert {row['tag'] for row in native.listing()['leases']} == set(leases)
        assert all(outcome['state'] == 'done' for outcome in kit.ledger.docs.values())
    finally:
        for identity in leases.values():
            native.release(identity)


def test_duplicate_attaches_and_changed_record_conflicts(kit):
    result = call(kit)
    assert call(kit) == result
    assert kit.processes[0].commands == ["load", "decide"]
    with pytest.raises(ValueError, match="different work"):
        kit.residents.request({**RECORD, "state": "changed"}, "first", kit.owner, lambda _stage: kit.model, lambda: True)


def test_current_authority_knows_queue_load_compute_and_result_stages(kit):
    seen = []
    def resolve(stage):
        seen.append(stage)
        return kit.model
    call(kit, resolve=resolve)
    assert set(seen) == {"queue", "load", "compute", "result"}
    seen.clear()
    call(kit, "warm", resolve=resolve)
    assert set(seen) == {"queue", "compute", "result"}
    seen.clear()
    call(kit, "warm", resolve=resolve)
    assert seen == ["queue", "result"]


def test_changed_host_implementation_or_calibration_does_not_reuse_old_model(kit):
    call(kit)
    kit.residents.implementation = "new-installed-implementation"
    call(kit, "new-code")
    calibrated = replace(kit.model, calibration={"temperature": 2})
    call(kit, "new-calibration", resolve=lambda _stage: calibrated)
    assert len(kit.processes) == 3
    assert all(process.commands == ["load", "decide"] for process in kit.processes)


def test_restart_does_not_replay_lost_or_completed_outcome(kit):
    call(kit)
    kit.residents.instance = "new-broker-instance"
    with pytest.raises(InterruptedError, match="not repeated"):
        call(kit)
    assert kit.processes[0].commands == ["load", "decide"]


def test_memory_admission_precedes_spawn_and_failure_does_not_load(kit):
    kit.table.admit = False
    with pytest.raises(RuntimeError, match="admitted against memory"):
        call(kit)
    assert not kit.processes


def test_queued_memory_revocation_releases_request_without_spawning(kit):
    allowed = [True]

    def acquire(_resources, _terms):
        kit.table.held.add("queued")
        return {"state": "queued", "id": "queued"}

    def wait(identity, seconds):
        allowed[0] = False
        return {"state": "queued", "id": identity}

    def resolve(_stage):
        if not allowed[0]:
            raise PermissionError("revoked")
        return kit.model

    kit.table.acquire, kit.table.wait = acquire, wait
    with pytest.raises(PermissionError):
        call(kit, resolve=resolve)
    assert not kit.processes and not kit.table.held


def test_malformed_scores_interrupt_without_replaying(kit):
    call(kit)
    original = kit.processes[0].exchange

    def exchange(message, connected):
        result = original(message, connected)
        result["result"]["scores"] = {"yes": float("nan"), "no": .2}
        return result

    kit.processes[0].exchange = exchange
    with pytest.raises(ValueError, match="probabilities"):
        call(kit, "malformed")
    assert kit.processes[0].ended and not kit.table.held
    with pytest.raises(InterruptedError, match="not repeated"):
        call(kit, "malformed")


def test_exchange_cancellation_stops_child_before_releasing_compute(kit):
    call(kit)
    process = kit.processes[0]
    stop = process.close
    stopped_while_held = []

    def fail(_message, _connected):
        raise InterruptedError("connection lost while inference is active")

    def close():
        stopped_while_held.append((kit.active[0], process.alive()))
        stop()

    process.exchange, process.close = fail, close
    with pytest.raises(InterruptedError):
        call(kit, "cancelled")
    assert stopped_while_held and all(held or not alive for held, alive in stopped_while_held)
    assert not kit.active[0] and not kit.table.held


def test_uncertain_stop_retains_compute_and_memory_until_child_is_proven_dead(kit):
    call(kit)
    process = kit.processes[0]
    stop = process.close

    def fail(*_args):
        raise InterruptedError("connection lost")

    def uncertain():
        raise RuntimeError("stop is uncertain")

    process.exchange, process.close = fail, uncertain
    try:
        with pytest.raises(RuntimeError, match="uncertain"):
            call(kit, "uncertain")
        assert kit.active[0] and kit.table.held and kit.residents.residents
    finally:
        process.close = stop


def test_owner_transfer_failure_keeps_uncertain_child_and_memory_tracked(kit):
    create, renew = kit.residents.backend, kit.table.renew
    stops = []

    def backend():
        process = create()
        stops.append(process.close)

        def uncertain():
            raise RuntimeError("stop is uncertain")

        process.close = uncertain
        return process

    def failed_transfer(*_args, **_kwargs):
        raise RuntimeError("owner transfer failed")

    kit.residents.backend, kit.table.renew = backend, failed_transfer
    try:
        with pytest.raises(RuntimeError, match="uncertain"):
            call(kit)
        assert kit.residents.residents and kit.table.held and kit.active[0]
    finally:
        kit.table.renew = renew
        kit.processes[0].close = stops[0]


def test_source_and_owner_authorization_required_even_for_cached_outcome(kit):
    call(kit)
    with pytest.raises(InterruptedError):
        call(kit, connected=lambda: False)
    with pytest.raises(PermissionError):
        call(kit, resolve=lambda _stage: None)
    owner = DecisionOwner(kit.owner.scope, kit.owner.pid, kit.owner.started - 1)
    with pytest.raises(InterruptedError, match="process identity"):
        kit.residents.request(RECORD, "new", owner, lambda _stage: kit.model, lambda: True)


@pytest.mark.parametrize("pid,started", [(0, None), (-1, 1), (True, 1), (1, None), (1, float("nan")), (1, 0)])
def test_missing_or_invalid_owner_process_proof_never_loads(kit, pid, started):
    owner = DecisionOwner(kit.owner.scope, pid, started)
    with pytest.raises(PermissionError):
        kit.residents.request(RECORD, "invalid-owner", owner, lambda _stage: kit.model, lambda: True)
    assert not kit.processes


def test_revocation_after_load_withholds_result_and_evicts(kit):
    calls = []

    def resolve(_stage):
        calls.append(True)
        if kit.processes and "decide" in kit.processes[0].commands:
            raise PermissionError("revoked")
        return kit.model

    with pytest.raises(PermissionError):
        call(kit, resolve=resolve)
    assert kit.processes[0].ended and not kit.table.held
    assert len(calls) > 1
    with pytest.raises(InterruptedError, match="not repeated"):
        call(kit)


def test_measured_residency_over_admission_stops_host(kit):
    call(kit)
    kit.processes[0].measured = kit.model.memory_bytes + 1
    with pytest.raises(RuntimeError, match="admitted peak"):
        call(kit, "second")
    assert kit.processes[0].ended and not kit.table.held


def test_idle_eviction_releases_memory_only_after_process_exit(kit):
    call(kit)
    kit.broker.idle_s = -1
    kit.residents.upkeep()
    assert kit.processes[0].ended
    assert not kit.table.held and not kit.residents.snapshot()


def test_idle_eviction_does_not_block_broker_upkeep_behind_other_compute(kit, monkeypatch):
    call(kit)
    kit.broker.idle_s = -1
    waits = []

    def busy(*_args, **options):
        waits.append(options["timeout"])
        return {"granted": False}

    with monkeypatch.context() as change:
        change.setattr(kit.broker, "claim", busy)
        kit.residents.upkeep()
    assert waits == [0]
    assert kit.table.held and not kit.processes[0].ended


def test_busy_registry_renews_every_resident_beyond_a_memory_lease_lifetime(kit):
    from poolhouse.serve import admission

    call(kit)
    second = replace(kit.model, identity="second-model")
    call(kit, "second", resolve=lambda _stage: second)
    clock = [0]
    expires = dict.fromkeys(kit.table.held, admission.KEEP_S)

    def renew(identity, **_terms):
        assert expires[identity] > clock[0]
        expires[identity] = clock[0] + admission.KEEP_S

    kit.table.renew = renew
    locked, release = threading.Event(), threading.Event()

    def work():
        with kit.residents.lock:
            locked.set()
            release.wait()

    thread = threading.Thread(target=work)
    thread.start()
    locked.wait()
    try:
        for _ in range(4):
            clock[0] += admission.KEEP_S // 2
            kit.residents.upkeep()
        assert all(expiry > clock[0] for expiry in expires.values())
        assert len(kit.table.held) == 2
    finally:
        release.set()
        thread.join()


def test_nonreading_host_pipe_remains_cancellable():
    from poolhouse.serve.decision_process import DecisionProcess

    read, write = os.pipe()
    os.set_blocking(write, False)
    with os.fdopen(read, "rb"), os.fdopen(write, "wb", buffering=0) as stream:
        with pytest.raises(BlockingIOError):
            while True:
                os.write(write, b"x" * 65536)
        process = DecisionProcess.__new__(DecisionProcess)
        process.process = SimpleNamespace(stdin=stream, poll=lambda: None)
        state = iter([True, False])
        with pytest.raises(InterruptedError, match="sending"):
            process._write(b"record", lambda: next(state))


def test_managed_host_cannot_be_created_without_broker_grant(monkeypatch):
    from poolhouse.serve.decision_process import DecisionProcess
    from poolhouse.serve.grant import NoGrant

    def refuse(*_args, **_kwargs):
        pytest.fail("An ungranted host must not inspect the managed environment")

    monkeypatch.setattr("poolhouse.serve.decision_process.Environment", refuse)
    with pytest.raises(NoGrant):
        DecisionProcess()


def test_actual_token_limit_refuses_before_any_model_forward(monkeypatch):
    from poolhouse.decide.base import Asked
    from poolhouse.decide.pointer_prompt import MAX_PROMPT_TOKENS
    from poolhouse.decide.types import DecideError, Option
    from poolhouse.serve.decision_host import HostedPointer

    monkeypatch.setattr("poolhouse.decide.pointer._torch", lambda: SimpleNamespace())
    def tokenizer(*_args, **_kwargs):
        return {"input_ids": SimpleNamespace(shape=(1, MAX_PROMPT_TOKENS + 1))}
    model = HostedPointer.__new__(HostedPointer)
    model._lock = threading.Lock()
    model._loaded = (tokenizer, None, None, "cpu", SimpleNamespace(temperature=1, details=None), {})
    with pytest.raises(DecideError, match="tokens; the limit"):
        model.probabilities(Asked("Question", "State", "State", (Option("yes"), Option("no"))))


@pytest.mark.parametrize("record", [{**RECORD, "argv": ["untrusted"]}, {**RECORD, "options": {}},
                                  {**RECORD, "abstain_below": float("nan")}, {**RECORD, "state": "x" * 200001}])
def test_invalid_records_never_load_model(kit, record):
    with pytest.raises(ValueError):
        kit.residents.request(record, "invalid", kit.owner, lambda _stage: kit.model, lambda: True)
    assert not kit.processes
