"""Synthetic filesystem checks for the inactive install transaction model."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "app" / "native-installer"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


inventory = load("inventory")
transaction = load("transaction")
log = load("transaction_log")


def make_plan(**changes):
    rows = [(role, path, str(index + 1), mode, hashlib.sha256(path.encode()).hexdigest())
            for index, (path, (role, mode)) in enumerate(sorted(inventory.REQUIRED.items()))]
    data = inventory.HEADER + "".join("\t".join(row) + "\n" for row in rows).encode()
    value = {"version": 1, "hardware": "1" * 64, "volume_group": "11111111-1111-1111-1111-111111111111",
             "data_volume": "22222222-2222-2222-2222-222222222222", "owner_uid": 501, "owner_auid": 501,
             "nonce": "2" * 64, "release": "3" * 64, "installer": "4" * 64, "policy": "5" * 64,
             "inventory": inventory.digest(data), "destinations": [list(p) for p in transaction.DESTINATIONS],
             "argv": list(transaction.FUTURE_ARGV), "code": {"helper": "6" * 40, "gui": "7" * 40}}
    value.update(changes)
    return transaction.Plan(transaction.canonical(value), data)


class TestFilesystem:
    """Model file contents independently from durable directory names."""
    __test__ = False

    def __init__(self):
        self.files = {}
        self.synced = {}
        self.names = {}
        self.durable_names = {}
        self.counter = 0
        self.log = []

    def write(self, name, data):
        if name in self.names:
            assert self.files[self.names[name]] == data
            return
        self.counter += 1
        self.names[name] = self.counter
        self.files[self.counter] = data

    def apply(self, effect):
        kind, name, data = effect.kind, effect.name, effect.data
        self.log.append((kind, name))
        if kind in ("write-exclusive-temp", "stage-exact-artifact"):
            self.write(name, data)
        elif kind == "fsync-file":
            inode = self.names[name]
            self.synced[inode] = self.files[inode]
        elif kind == "rename-log":
            self.names[name] = self.names.pop("log.next")
        elif kind == "fsync-directory":
            selected = (lambda key: key.startswith(("GUI/", "Helper/", "Services/"))) if name == "payload" else (lambda key: key.startswith("log"))
            self.durable_names = {k: v for k, v in self.durable_names.items() if not selected(k)}
            self.durable_names.update({k: v for k, v in self.names.items() if selected(k)})
        elif kind == "readback-exact-artifact":
            assert self.files[self.names[name]] == data
        else:
            raise AssertionError(kind)

    def crash(self):
        self.names = self.durable_names.copy()
        self.files = self.synced.copy()

    def observations(self):
        return {name: self.files[inode].decode() for name, inode in self.names.items()
                if name.startswith(("GUI/", "Helper/", "Services/"))}

    def step(self, plan):
        data = self.files[self.names["log"]] if "log" in self.names else None
        return log.next_step(plan, data, self.observations())


def finish(fs, plan):
    for _ in range(2 * 64 + 4):
        step = fs.step(plan)
        if step.result is not None:
            assert not step.effects
            return step.result
        for effect in step.effects:
            fs.apply(effect)
    raise AssertionError("Model failed to terminate")


def test_every_effect_crash_resumes_exact_transaction():
    plan = make_plan()
    baseline = TestFilesystem()
    result = finish(baseline, plan)
    for crash_at in range(len(baseline.log) + 1):
        fs = TestFilesystem()
        count = 0
        while count < crash_at:
            step = fs.step(plan)
            for effect in step.effects:
                if count == crash_at:
                    break
                fs.apply(effect)
                count += 1
        fs.crash()
        assert finish(fs, plan) == result
        before = fs.log.copy()
        assert fs.step(plan).result == result
        assert fs.log == before


def test_file_sync_does_not_preserve_unsynced_directory_rename():
    fs = TestFilesystem()
    fs.write("log.next", b"pending")
    fs.apply(log.Effect("fsync-file", "log.next", b""))
    fs.apply(log.Effect("rename-log", "log", b""))
    fs.crash()
    assert "log" not in fs.names


def test_directory_sync_does_not_preserve_unsynced_file_content():
    fs = TestFilesystem()
    fs.write("log.next", b"pending")
    fs.apply(log.Effect("rename-log", "log", b""))
    fs.apply(log.Effect("fsync-directory", "transaction", b""))
    fs.crash()
    with pytest.raises(KeyError):
        fs.files[fs.names["log"]]


@pytest.mark.parametrize("field,value", [("nonce", "0" * 64), ("version", True), ("owner_uid", True),
    ("owner_auid", 502), ("argv", ["sh", "payload"]), ("destinations", []), ("inventory", "a" * 64),
    ("code", {"helper": "6" * 40}), ("data_volume", "../disk"), ("hardware", "A" * 64)])
def test_bad_plan_refuses(field, value):
    with pytest.raises(ValueError):
        log.next_step(make_plan(**{field: value}), None, {})


@pytest.mark.parametrize("field", ["hardware", "nonce", "release", "installer", "policy", "owner_uid", "volume_group", "code"])
def test_changed_semantics_bind_log(field):
    plan = make_plan()
    value = plan.value()
    replacement = {"owner_uid": 502, "volume_group": "33333333-3333-3333-3333-333333333333", "code": {"helper": "8" * 40, "gui": "7" * 40}}.get(field, "9" * 64)
    value[field] = replacement
    if field == "owner_uid":
        value["owner_auid"] = replacement
    changed = transaction.Plan(transaction.canonical(value), plan.inventory)
    assert changed.digest() != plan.digest()
    fs = TestFilesystem()
    finish(fs, plan)
    with pytest.raises(ValueError):
        fs.step(changed)


def test_unknown_duplicate_noncanonical_manifest_refuses():
    plan = make_plan()
    for data in [plan.manifest.replace(b'{', b'{"version":1,', 1), plan.manifest + b"\n",
                 transaction.canonical(dict(plan.value(), extra=1))]:
        with pytest.raises(ValueError):
            transaction.Plan(data, plan.inventory).digest()


@pytest.mark.parametrize("change", [lambda d: d.replace(b"\n", b"\r\n"), lambda d: d + d.splitlines(keepends=True)[1],
    lambda d: d.replace(b"\t0755\t", b"\t0777\t"), lambda d: d.replace(b"Contents/Info.plist", b"Contents/../Info.plist"),
    lambda d: d.replace(b"helper\t", b"unknown\t"), lambda d: d[:-1]])
def test_inventory_aliases_and_unknowns_refuse(change):
    with pytest.raises(ValueError):
        inventory.parse(change(make_plan().inventory))


def test_missing_corrupt_foreign_and_skipped_prefix_refuse():
    plan = make_plan()
    with pytest.raises(ValueError):
        log.next_step(plan, None, {}, existing_authority=True)
    fs = TestFilesystem()
    finish(fs, plan)
    for data in [None, b"{}", b"corrupt", log._record(make_plan(nonce="8" * 64), "COMMITTED", 5),
                 log._record(plan, "INSTALLING", 1)]:
        with pytest.raises(ValueError):
            log.next_step(plan, data, fs.observations())
    for observations in [{}, dict(fs.observations(), extra="1" * 64),
                         dict.fromkeys(fs.observations(), "f" * 64)]:
        with pytest.raises(ValueError):
            log.next_step(plan, log._record(plan, "COMMITTED", 5), observations)


def test_effect_order_and_committed_result_binding():
    fs = TestFilesystem()
    plan = make_plan()
    assert finish(fs, plan) == plan.result()
    assert fs.log[:4] == [("write-exclusive-temp", "log.next"), ("fsync-file", "log.next"),
                          ("rename-log", "log"), ("fsync-directory", "transaction")]
    for index, (kind, _) in enumerate(fs.log):
        if kind == "readback-exact-artifact":
            assert [k for k, _ in fs.log[index - 3:index]] == ["stage-exact-artifact", "fsync-file", "fsync-directory"]
    data = json.loads(log._record(plan, "COMMITTED", 5))
    data["result"] = "other"
    with pytest.raises(ValueError):
        log.next_step(plan, transaction.canonical(data), fs.observations())
