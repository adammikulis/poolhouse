"""Untrusted frozen installation transaction descriptions."""
import hashlib
import json
import re
from dataclasses import dataclass

import inventory

DOMAIN = b"poolhouse.native-recovery-install.v1\0"
DESTINATIONS = (
    ("Helper", "/Library/Application Support/Poolhouse/Authority"),
    ("GUI", "/Library/Application Support/Poolhouse/Approval"),
    ("Services", "/Library/LaunchDaemons"),
)
FUTURE_ARGV = ("NativeRecoveryInstall", "--commit-frozen-transaction")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _object(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError("Duplicate field.")
        result[key] = value
    return result


def decode(data: bytes):
    if type(data) is not bytes or len(data) > 32768:
        raise ValueError("Invalid transaction data.")
    try:
        value = json.loads(data, object_entries_hook=_object)
        if canonical(value) != data:
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Invalid transaction data.") from None


def hex32(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None and value != "0" * 64


@dataclass(frozen=True)
class Plan:
    """A public description with no installation authorization."""

    manifest: bytes
    inventory: bytes

    def value(self):
        value = decode(self.manifest)
        fields = {"version", "hardware", "volume_group", "data_volume", "owner_uid", "owner_auid",
                  "nonce", "release", "installer", "policy", "inventory", "destinations", "argv", "code"}
        if type(value) is not dict or set(value) != fields or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Invalid transaction plan.")
        for key in ("hardware", "nonce", "release", "installer", "policy", "inventory"):
            if not hex32(value[key]):
                raise ValueError("Invalid transaction plan.")
        for key in ("volume_group", "data_volume"):
            if type(value[key]) is not str or not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", value[key]) or value[key] == "00000000-0000-0000-0000-000000000000":
                raise ValueError("Invalid transaction plan.")
        if value["volume_group"] == value["data_volume"]:
            raise ValueError("Invalid transaction plan.")
        if any(type(value[k]) is not int or not 0 < value[k] < 2**32 - 1 for k in ("owner_uid", "owner_auid")) or value["owner_uid"] != value["owner_auid"]:
            raise ValueError("Invalid transaction plan.")
        if value["destinations"] != [list(entry) for entry in DESTINATIONS] or value["argv"] != list(FUTURE_ARGV):
            raise ValueError("Invalid transaction plan.")
        code = value["code"]
        if type(code) is not dict or set(code) != {"helper", "gui"}:
            raise ValueError("Invalid transaction plan.")
        for pin in code.values():
            if type(pin) is not str or not re.fullmatch(r"[0-9a-f]{40}", pin) or pin == "0" * 40:
                raise ValueError("Invalid transaction plan.")
        if value["inventory"] != inventory.digest(self.inventory):
            raise ValueError("Invalid transaction plan.")
        return value

    def digest(self) -> str:
        self.value()
        return hashlib.sha256(DOMAIN + self.manifest).hexdigest()

    def artifacts(self):
        self.value()
        return inventory.parse(self.inventory)

    def result(self) -> bytes:
        return canonical({"kind": "model-committed", "plan": self.digest()})
