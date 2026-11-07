"""The Android protocol classes against the vectors the node wrote: a ratchet on the port to charter version 4.

The node regenerates `tests/fixtures/phone-founding-vectors.json`, `phone-act-vectors.json` and `phone-admit-vectors.json` for charter version 4
(`claim` and `floors` in the charter, `first.delegate`, the accept domain `poolhouse/pool-accept/v3`). The Java protocol classes under
`app/android/src/main/java/app/poolhouse/phone/` and the Gradle tests beside them still read charter version 3, `strength` and
`poolhouse/pool-accept/v2`. No lane runs Gradle, so nothing else says so. This test reads both sides as text, states what each reads or
emits as a named fact, and compares them:

* `PENDING_PORT` names every fact on which they disagree today, with the value each side holds. That is the Android port, written down.
* `AGREED` names every fact they agree on, so a disagreement that appears where there was none is caught the same way.
* The test fails, naming the side that changed, when a fact is not what the table says: when the port lands in part (a Java class moves to
  version 4: the fact now agrees, take it out of `PENDING_PORT`), and when the vectors change again (they say something the Java does not read).
  Landing the whole port empties `PENDING_PORT`; the file then says that the two agree.

It reads no Android tool and runs no Gradle: it is regular expressions over the Java constants and the decoded bytes of the vectors.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "android" / "src" / "main" / "java" / "app" / "poolhouse" / "phone"
GRADLE = ROOT / "app" / "android" / "src" / "test" / "java" / "app" / "poolhouse" / "phone"
FIXTURES = ROOT / "tests" / "fixtures"

ACT_FIELDS = ["at_ms", "body", "epoch", "kind", "nonce", "not_after", "pool", "prev", "signatures", "v"]
CHARTER_FIELDS_V3 = ["admins", "at_ms", "epoch", "first", "from", "holds", "mode", "name", "policy", "project", "recover_delay_days", "succession", "thresholds", "v"]
ADMIN_FIELDS_V3 = ["acceptance", "alg", "key", "label", "person", "role", "since", "strength"]
ADMIN_FIELDS_V4 = ["acceptance", "alg", "claim", "key", "label", "person", "role", "since"]
ACCEPT_FIELDS_V3 = ["alg", "anchor", "key", "person", "prev", "role", "since", "strength", "v"]
ACCEPT_FIELDS_V4 = ["alg", "anchor", "claim", "key", "person", "prev", "role", "since", "v"]

# Where the Java and the vectors disagree today: the work of the Android port. Each side's value is what that side reads or says now.
# When a class is ported its facts agree and leave this table; when the vectors change again their side of an entry changes.
PENDING_PORT = {
    "charter.version": {"java": 3, "vectors": 4},
    "charter.fields": {"java": CHARTER_FIELDS_V3, "vectors": sorted([*CHARTER_FIELDS_V3, "floors"])},
    "charter.first.fields": {"java": ["cert", "name"], "vectors": ["cert", "delegate", "name"]},
    "charter.admin.fields": {"java": ADMIN_FIELDS_V3, "vectors": ADMIN_FIELDS_V4},
    "accept.domain.FoundingCharter": {"java": 2, "vectors": 3},
    "accept.message.v.FoundingCharter": {"java": 2, "vectors": 3},
    "accept.message.fields.FoundingCharter": {"java": ACCEPT_FIELDS_V3, "vectors": ACCEPT_FIELDS_V4},
    "key.held_how_field.main": {"java": ["claim", "strength"], "vectors": ["claim"]},
    "key.held_how_field.gradle": {"java": ["claim", "strength"], "vectors": ["claim"]},
    "accept.domain.gradle": {"java": 2, "vectors": 3},
}

# What the Java and the vectors agree on, so that a disagreement where there was none is named the same way.
AGREED = {
    "admin_add.entry.fields": ADMIN_FIELDS_V4,
    "accept.domain.AddRequest": 3,
    "accept.message.v.AddRequest": 3,
    "accept.message.fields.AddRequest": ACCEPT_FIELDS_V4,
    "act.domain.AdminAddAct": 2,
    "act.domain.GovernanceAct": 2,
    "act.v.AdminAddAct": 2,
    "act.v.GovernanceAct": 2,
    "act.fields.AdminAddAct": ACT_FIELDS,
    "act.fields.GovernanceAct": ACT_FIELDS,
}


# -- the Java side ------------------------------------------------------------------------------------------------------------------


class Unreadable(AssertionError):
    """A constant this test reads from the Java is no longer where it was: the Java changed shape, so the extractor needs the same change."""


def java(directory: Path, name: str) -> str:
    return (directory / name).read_text(encoding="utf-8")


def found(pattern: str, text: str, where: str, what: str, flags: int = 0) -> re.Match:
    match = re.search(pattern, text, flags)
    if match is None:
        raise Unreadable(f"{where}: cannot find {what} (pattern {pattern!r}); the Java source changed shape, so tests/test_android_vectors_agree.py must read the new shape")
    return match


def one(values: list):
    """The single value when they all agree, else the sorted distinct values."""
    distinct = sorted({json.dumps(v): v for v in values}.items())
    return distinct[0][1] if len(distinct) == 1 else [v for _, v in distinct]


def quoted_names(text: str) -> list[str]:
    return sorted(set(re.findall(r'"([A-Za-z_]+)"', text)))


def fields_of(text: str, variable: str, where: str) -> list[str]:
    """The names of `fields(variable, "a", "b", ...)`: what a class refuses to read a JSON object without."""
    match = found(rf'\bfields\(\s*{variable}\s*,\s*((?:"[a-z_]+"\s*,?\s*)+)\)', text, where, f"fields({variable}, ...)")
    return quoted_names(match.group(1))


def domain_versions(text: str, kind: str, where: str) -> list[int]:
    versions = [int(v) for v in re.findall(rf'poolhouse/pool-{kind}/v(\d+)\\n', text)]
    if not versions:
        raise Unreadable(f"{where}: no poolhouse/pool-{kind}/vN domain found; the Java source changed shape")
    return versions


def accept_message(text: str, builder: str, where: str) -> tuple[int, list[str]]:
    """The version and the field names of the acceptance a class builds: the `new JSONObject().put(...)...;` statement bound to ``builder``
    and the `for (String key : new String[]{...}) builder.put(key, ...)` that adds the rest."""
    statement = found(rf'JSONObject {builder} = (new JSONObject\(\)[^;]*);', text, where, f"the statement that makes {builder}").group(1)
    names = re.findall(r'\.put\("(\w+)"', statement)
    for loop in re.finditer(rf'new String\[\]\{{([^}}]*)\}}\)\s*{builder}\.put\(', text):
        names += quoted_names(loop.group(1))
    version = int(found(r'\.put\("v",\s*(\d+)\)', statement, where, f'the "v" of {builder}').group(1))
    return version, sorted(set(names))


def java_facts() -> dict:
    founding_charter = java(MAIN, "FoundingCharter.java")
    add_request = java(MAIN, "AddRequest.java")
    admin_add = java(MAIN, "AdminAddAct.java")
    governance = java(MAIN, "GovernanceAct.java")
    main_sources = [java(MAIN, p.name) for p in sorted(MAIN.glob("*.java"))]
    tests = {name: java(GRADLE, f"{name}.java") for name in ("PhoneFoundingTest", "PoolKeyTest", "AdmitTest")}
    low, high = (int(n) for n in found(r'number\(\s*c\s*,\s*"v"\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', founding_charter, "FoundingCharter.java", "the charter version it reads").groups())
    facts = {
        "charter.version": low if low == high else [low, high],
        "charter.fields": fields_of(founding_charter, "c", "FoundingCharter.java"),
        "charter.first.fields": fields_of(founding_charter, "first", "FoundingCharter.java"),
        "charter.admin.fields": fields_of(founding_charter, "k", "FoundingCharter.java"),
        "admin_add.entry.fields": fields_of(admin_add, "entry", "AdminAddAct.java"),
        "accept.domain.FoundingCharter": one(domain_versions(founding_charter, "accept", "FoundingCharter.java")),
        "accept.domain.AddRequest": one(domain_versions(add_request, "accept", "AddRequest.java")),
        "act.domain.AdminAddAct": one(domain_versions(admin_add, "act", "AdminAddAct.java")),
        "act.domain.GovernanceAct": one(domain_versions(governance, "act", "GovernanceAct.java")),
        "act.v.AdminAddAct": int(found(r'number\(\s*act\s*,\s*"v"\s*,\s*(\d+)\s*,\s*\1\s*\)', admin_add, "AdminAddAct.java", "the act version it reads").group(1)),
        "act.v.GovernanceAct": int(found(r'number\(\s*act\s*,\s*"v"\s*,\s*(\d+)\s*,\s*\1\s*\)', governance, "GovernanceAct.java", "the act version it reads").group(1)),
        "act.fields.AdminAddAct": fields_of(admin_add, "act", "AdminAddAct.java"),
        "act.fields.GovernanceAct": fields_of(governance, "act", "GovernanceAct.java"),
        "key.held_how_field.main": sorted(set(re.findall(r'"(strength|claim)"', "\n".join(main_sources)))),
        "key.held_how_field.gradle": sorted(set(re.findall(r'"(strength|claim)"', "\n".join(tests.values())))),
        "accept.domain.gradle": one([v for name in ("PhoneFoundingTest", "PoolKeyTest") for v in domain_versions(tests[name], "accept", f"{name}.java")]),
    }
    for builder, text, name in (("acceptance", founding_charter, "FoundingCharter"), ("terms", add_request, "AddRequest")):
        version, names = accept_message(text, builder, f"{name}.java")
        facts[f"accept.message.v.{name}"] = version
        facts[f"accept.message.fields.{name}"] = names
    return facts


# -- the vectors side ---------------------------------------------------------------------------------------------------------------


def vector(name: str) -> dict:
    return json.loads((FIXTURES / f"phone-{name}-vectors.json").read_text(encoding="utf-8"))


def signed(hex_bytes: str, kind: str) -> tuple[int, dict]:
    """The domain version and the JSON of the bytes a vector says a key signs."""
    domain, _, body = bytes.fromhex(hex_bytes).decode("utf-8").partition("\n")
    match = re.fullmatch(rf"poolhouse/pool-{kind}/v(\d+)", domain)
    assert match, f"a vector holds signing bytes of the domain {domain!r}, not poolhouse/pool-{kind}/vN"
    return int(match.group(1)), json.loads(body)


def keys_everywhere(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_everywhere(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_everywhere(v)}
    return set()


def vector_facts() -> dict:
    founding, act, admit = vector("founding"), vector("act"), vector("admit")
    charter = founding["charter"]
    accepts = [signed(h, "accept") for h in [founding["signing_bytes"]] + [a["bytes"] for a in founding["acceptances"]]]
    admit_accept = signed(admit["acceptance_bytes"], "accept")
    admit_act = signed(admit["act_bytes"], "act")
    rows = [signed(row["bytes"], "act") for row in act["rows"]]
    entry_fields = {tuple(sorted(a)) for a in charter["admins"]}
    assert len(entry_fields) == 1, f"the admin keys of the founding vector do not all have the same fields: {entry_fields}"
    messages = [m for _, m in accepts]
    held = keys_everywhere([founding, act, admit, *messages, admit_accept[1]]) & {"strength", "claim"}
    return {
        "charter.version": charter["v"],
        "charter.fields": sorted(charter),
        "charter.first.fields": sorted(charter["first"]),
        "charter.admin.fields": sorted(entry_fields.pop()),
        "admin_add.entry.fields": sorted(admit["act"]["body"]["entry"]),
        "accept.domain.FoundingCharter": one([v for v, _ in accepts]),
        "accept.domain.AddRequest": admit_accept[0],
        "accept.domain.gradle": one([v for v, _ in accepts]),
        "accept.message.v.FoundingCharter": one([m["v"] for m in messages]),
        "accept.message.v.AddRequest": admit_accept[1]["v"],
        "accept.message.fields.FoundingCharter": one([sorted(m) for m in messages]),
        "accept.message.fields.AddRequest": sorted(admit_accept[1]),
        "act.domain.AdminAddAct": admit_act[0],
        "act.domain.GovernanceAct": one([v for v, _ in rows]),
        "act.v.AdminAddAct": admit["act"]["v"],
        "act.v.GovernanceAct": one([r["act"]["v"] for r in act["rows"]]),
        "act.fields.AdminAddAct": sorted(admit["act"]),
        "act.fields.GovernanceAct": one([sorted(r["act"]) for r in act["rows"]]),
        "key.held_how_field.main": sorted(held),
        "key.held_how_field.gradle": sorted(held),
    }


# -- the ratchet ---------------------------------------------------------------------------------------------------------------------


def problems(java_side: dict, vector_side: dict, pending: dict, agreed: dict) -> list[str]:
    """Every way the two sides differ from what the tables say, each naming the side that moved and what to do about it."""
    out = []
    for name in sorted(set(java_side) | set(vector_side) | set(pending) | set(agreed)):
        if name in pending and name in agreed:
            out.append(f"{name}: is in both PENDING_PORT and AGREED; it is one or the other")
        elif name not in java_side or name not in vector_side:
            side = "Java" if name not in java_side else "vectors"
            out.append(f"{name}: the {side} side is no longer read by this test; keep the extractor and the tables together")
        elif name not in pending and name not in agreed:
            out.append(f"{name}: a fact this test reads that no table lists (java {java_side[name]!r}, vectors {vector_side[name]!r}); add it to PENDING_PORT or AGREED")
        else:
            want_java, want_vectors = (pending[name]["java"], pending[name]["vectors"]) if name in pending else (agreed[name], agreed[name])
            moved = []
            if java_side[name] != want_java:
                moved.append(f"the JAVA side changed (it reads {java_side[name]!r}; the table has {want_java!r})")
            if vector_side[name] != want_vectors:
                moved.append(f"the VECTORS changed (they say {vector_side[name]!r}; the table has {want_vectors!r})")
            if not moved:
                continue
            now_agree = java_side[name] == vector_side[name]
            if name in pending and now_agree:
                then = "they agree now: move it from PENDING_PORT to AGREED (this part of the port landed, or the vectors went back to what the Java reads)"
            elif name in pending:
                then = "they still disagree, differently: update this entry of PENDING_PORT to what the port now has to do"
            else:
                then = "they agreed and no longer do: either the port broke it or the vectors moved on; a new entry of PENDING_PORT is the port left to do"
            out.append(f"{name}: {' and '.join(moved)}; {then}")
    return out


def report(found_problems: list[str]) -> str:
    return "The Android port to charter version 4 is not where tests/test_android_vectors_agree.py says it is:\n  - " + "\n  - ".join(found_problems)


def test_the_android_classes_and_the_node_vectors_disagree_exactly_where_the_port_is_pending():
    java_side, vector_side = java_facts(), vector_facts()
    for name, entry in PENDING_PORT.items():
        assert entry["java"] != entry["vectors"], f"{name} is in PENDING_PORT but its two sides are the same: it belongs in AGREED"
    found_problems = problems(java_side, vector_side, PENDING_PORT, AGREED)
    assert not found_problems, report(found_problems)


def test_the_ratchet_goes_red_naming_the_side_that_moved_in_either_direction():
    """The comparison itself, on small tables of its own, so the real Java and vectors do not decide what it is shown to do."""
    pending = {"version": {"java": 3, "vectors": 4}, "field": {"java": ["strength"], "vectors": ["claim"]}}
    agreed = {"domain": 2}
    java_side = {"version": 3, "field": ["strength"], "domain": 2}
    vector_side = {"version": 4, "field": ["claim"], "domain": 2}
    assert problems(java_side, vector_side, pending, agreed) == []

    only = problems({**java_side, "version": 4}, vector_side, pending, agreed)
    assert len(only) == 1 and only[0].startswith("version:") and "JAVA side changed" in only[0] and "VECTORS" not in only[0], only
    assert "they agree now: move it from PENDING_PORT to AGREED" in only[0], only

    only = problems(java_side, {**vector_side, "version": 5}, pending, agreed)
    assert len(only) == 1 and only[0].startswith("version:") and "VECTORS changed" in only[0] and "JAVA" not in only[0], only
    assert "still disagree, differently" in only[0], only

    only = problems(java_side, {**vector_side, "domain": 3}, pending, agreed)
    assert len(only) == 1 and only[0].startswith("domain:") and "VECTORS changed" in only[0] and "JAVA" not in only[0], only
    assert "agreed and no longer do" in only[0], only

    only = problems({**java_side, "domain": 3}, vector_side, pending, agreed)
    assert len(only) == 1 and only[0].startswith("domain:") and "JAVA side changed" in only[0] and "VECTORS" not in only[0], only

    only = problems({**java_side, "extra": 1}, {**vector_side, "extra": 2}, pending, agreed)
    assert len(only) == 1 and only[0].startswith("extra:") and "no table lists" in only[0], only

    only = problems(java_side, vector_side, {"version": pending["version"]}, agreed)
    assert len(only) == 1 and only[0].startswith("field:") and "no table lists" in only[0], only

    only = problems({k: v for k, v in java_side.items() if k != "version"}, vector_side, pending, agreed)
    assert len(only) == 1 and only[0].startswith("version:") and "Java side is no longer read" in only[0], only
