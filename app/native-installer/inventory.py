"""Parse untrusted native capture inventories."""
import hashlib
import re
from dataclasses import dataclass

HEADER = b"poolhouse-native-candidate-v1\n"
REQUIRED = {
    "Helper/PoolhouseAuthority": ("helper", "0755"),
    "GUI/PoolhouseApproval.app/Contents/MacOS/PoolhouseApproval": ("gui", "0755"),
    "GUI/PoolhouseApproval.app/Contents/Info.plist": ("gui", "0644"),
    "GUI/PoolhouseApproval.app/Contents/_CodeSignature/CodeResources": ("gui", "0644"),
    "Services/app.poolhouse.authority.plist": ("service", "0644"),
}


@dataclass(frozen=True)
class Artifact:
    role: str
    path: str
    size: int
    mode: str
    digest: str


def parse(data: bytes) -> tuple[Artifact, ...]:
    """Return bounded inventory data without establishing provenance."""
    if type(data) is not bytes or len(data) > 16384 or not data.startswith(HEADER):
        raise ValueError("Invalid native inventory.")
    rows = data[len(HEADER):].splitlines(keepends=True)
    if not 1 <= len(rows) <= 64:
        raise ValueError("Invalid native inventory.")
    result = []
    previous = ""
    paths = set()
    for row in rows:
        if not row.endswith(b"\n"):
            raise ValueError("Invalid native inventory.")
        try:
            role, path, size, mode, digest = row[:-1].decode("ascii").split("\t")
        except (ValueError, UnicodeError):
            raise ValueError("Invalid native inventory.") from None
        if path <= previous:
            raise ValueError("Invalid native inventory.")
        previous = path
        expected = REQUIRED.get(path)
        if expected is None:
            prefix = "GUI/PoolhouseApproval.app/Contents/Resources/"
            name = path.removeprefix(prefix)
            if not path.startswith(prefix) or name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError("Invalid native inventory.")
            expected = ("gui", "0644")
        if (role, mode) != expected or path in paths or not re.fullmatch(r"0|[1-9][0-9]{0,7}", size):
            raise ValueError("Invalid native inventory.")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid native inventory.")
        paths.add(path)
        result.append(Artifact(role, path, int(size), mode, digest))
    if not REQUIRED.keys() <= paths or sum(item.size for item in result) > 64 * 1024 * 1024:
        raise ValueError("Invalid native inventory.")
    return tuple(result)


def digest(data: bytes) -> str:
    """Identify exact valid inventory bytes."""
    parse(data)
    return hashlib.sha256(data).hexdigest()
