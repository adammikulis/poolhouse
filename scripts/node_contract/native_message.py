"""Closed original-message DATA grammar and Node tables; no exporter or admission."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .common import keys

PRIMITIVES = {"Unit": 0, "String": 8, "Uuid": 16, "Bool": 1, "I64": 8, "Path": 9, "Timestamp": 12, "Number": 8}
ENVELOPE = {"Version", "SourceFeature", "Platform", "Launch", "Occurrence", "Instance", "CapturedKind", "ActorContext", "OriginalRequest"}
NAME = re.compile(r"[A-Z][A-Za-z0-9]*\Z")


def load(root: Path) -> dict:
    """Validate the finite grammar; accepted declarations are still untrusted data."""
    value = tomllib.loads((root / "native_boundaries/native_message.toml").read_text())
    required = {"version", "status", "source_feature", "source_feature_name", "source_serde_features", "envelope", "event_kind", "record", "union", "optional", "list", "map"}
    keys(value, required, required)
    if type(value["version"]) is not int or value["version"] != 1 or value["status"] != "untrusted-data-only":
        raise ValueError("unsupported native message data profile")
    _tag(value["source_feature"])
    if not isinstance(value["source_feature_name"], str) or not value["source_feature_name"]:
        raise ValueError("native message source feature name is missing")
    if (value["source_serde_features"] != ["arbitrary_precision", "preserve_order", "raw_value"]
            or not isinstance(value["envelope"], list) or len(value["envelope"]) != len(ENVELOPE)
            or any(not isinstance(item, str) for item in value["envelope"]) or set(value["envelope"]) != ENVELOPE or value["envelope"][-1] != "OriginalRequest"):
        raise ValueError("unsupported source representation or incomplete envelope")
    kinds = {}
    for row in _rows(value, "event_kind"):
        keys(row, {"name", "tag"}, {"name", "tag"})
        _name(row["name"])
        _tag(row["tag"])
        if row["name"] in kinds or row["tag"] in kinds.values():
            raise ValueError("duplicate native event kind")
        kinds[row["name"]] = row["tag"]
    definitions = _definitions(value, kinds)
    if definitions.get("OriginalRequest", {}).get("kind") != "union":
        raise ValueError("native message has no original request union")
    minimum = dict(PRIMITIVES)
    for _ in definitions:
        for name, row in definitions.items():
            candidate = None
            if row["kind"] == "record" and all(f["type"] in minimum for f in row["fields"]):
                candidate = sum(minimum[f["type"]] for f in row["fields"])
            elif row["kind"] == "union" and any(c["type"] in minimum for c in row["cases"]):
                candidate = 2 + min(minimum[c["type"]] for c in row["cases"] if c["type"] in minimum)
            elif row["kind"] in {"optional", "list", "map"}:
                candidate = 1 if row["kind"] == "optional" else 8
            if candidate is not None:
                minimum[name] = min(minimum.get(name, candidate), candidate)
    if set(minimum) != set(definitions) or any(row["kind"] == "list" and minimum[row["item"]] == 0 for row in definitions.values()):
        raise ValueError("nonproductive or zero-width native message repetition")
    return {**value, "types": list(definitions.values()), "minimum": minimum}


def _definitions(value: dict, kinds: dict, primitives: dict | None = None, *, allow_empty: bool = False) -> dict:
    atoms = PRIMITIVES if primitives is None else primitives
    definitions = {name: {"name": name, "kind": "primitive"} for name in atoms}
    for kind in ["record", "union", "optional", "list", "map"]:
        rows = [] if allow_empty and value[kind] == [] else _rows(value, kind)
        for row in rows:
            _name(row.get("name"))
            name = row["name"]
            if name in definitions:
                raise ValueError("duplicate native message type")
            expected = {"name", "fields" if kind == "record" else "cases" if kind == "union" else "item"}
            keys(row, expected, expected)
            definitions[name] = {**row, "kind": kind}
    for row in definitions.values():
        _validate_references(row, definitions, kinds)
    return definitions


def _validate_references(row: dict, definitions: dict, kinds: dict) -> None:
    if row["kind"] == "record":
        names = set()
        for field in _rows(row, "fields"):
            keys(field, {"name", "type"}, {"name", "type"})
            if not isinstance(field["name"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", field["name"]) or field["name"] in names:
                raise ValueError("invalid or duplicate native message field")
            names.add(field["name"])
            _reference(field["type"], definitions)
    elif row["kind"] == "union":
        names, tags = set(), set()
        for case in _rows(row, "cases"):
            keys(case, {"name", "tag", "type", "event_kind"}, {"name", "tag", "type"})
            _name(case["name"])
            _tag(case["tag"])
            _reference(case["type"], definitions)
            if case["name"] in names or case["tag"] in tags:
                raise ValueError("duplicate native message variant")
            names.add(case["name"])
            tags.add(case["tag"])
            if "event_kind" in case and (not isinstance(case["event_kind"], str) or case["event_kind"] not in kinds):
                raise ValueError("unknown native event kind mapping")
    elif row["kind"] != "primitive":
        _reference(row["item"], definitions)


def _rows(value: dict, name: str) -> list[dict]:
    rows = value[name]
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("native message grammar requires nonempty tables")
    return rows


def _name(value: object) -> None:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError("invalid native message type name")


def _tag(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("native message tag is not a nonzero u16")


def _reference(value: object, definitions: dict) -> None:
    if not isinstance(value, str) or value not in definitions:
        raise ValueError("unknown native message type reference")


def render(model: dict) -> str:
    """Render the sole closed parser grammar, not a second transport or DTO API."""
    ids = {row["name"]: i for i, row in enumerate(model["types"])}
    kinds = {row["name"]: row["tag"] for row in model["event_kind"]}
    out = ["// Generated from native_boundaries/native_message.toml; UNTRUSTED DATA ONLY.",
           "use super::{Case, EnvelopeField, Type};", f"pub(super) const VERSION: u16 = {model['version']};",
           f"pub(super) const SOURCE_FEATURE: u16 = {model['source_feature']};",
           f"pub(super) const ORIGINAL_REQUEST: usize = {ids['OriginalRequest']};", f"pub(super) const VALUE: usize = {ids['Value']};", f"pub(super) const STOP_TARGET: usize = {ids['StopTarget']};", "pub(super) const ENVELOPE: &[EnvelopeField] = &[" ]
    out.extend(f"    EnvelopeField::{name}," for name in model["envelope"])
    out.extend(["];", "pub(super) const TYPES: &[Type] = &["])
    for row in model["types"]:
        kind = row["kind"]
        if kind == "primitive":
            expression = "Type::" + row["name"]
        elif kind == "record":
            expression = "Type::Record(&[" + ", ".join(str(ids[f["type"]]) for f in row["fields"]) + "])"
        elif kind == "union":
            expression = "Type::Union(&[" + ", ".join("Case { tag: " + str(c["tag"]) + ", item: " + str(ids[c["type"]]) + ", kind: " + str(kinds.get(c.get("event_kind"), 0)) + " }" for c in row["cases"]) + "])"
        else:
            expression = f"Type::{kind.capitalize()}({ids[row['item']]})"
        out.append(f"    {expression}, // {row['name']}")
    out.extend(["];", "pub(super) const MINIMUM: &[usize] = &[" + ", ".join(str(model["minimum"][row["name"]]) for row in model["types"]) + "];", ""])
    return "\n".join(out)


def render_producer(model: dict) -> str:
    """Finite private Codex source binding; the canonical grammar owns tags/order."""
    types = {row['name']: row for row in model['types']}
    structs = {name: 'crate::' + name + 'Request' for name in ['PreToolUse', 'PostToolUse', 'PreCompact', 'PostCompact', 'UserPromptSubmit', 'SessionEnd', 'Interrupt', 'SessionStart', 'Stop']}
    structs.update({'PermissionRequest': 'crate::PermissionRequestRequest', 'Subagent': 'crate::SubagentHookContext', 'ModelRequest': 'crate::ModelRequestHookInput', 'TokenUsage': 'codex_protocol::protocol::TokenUsage', 'Legacy': 'crate::HookPayload', 'AfterAgent': 'crate::HookEventAfterAgent'})
    unions = {'SessionStartSource': 'crate::SessionStartSource', 'SessionStartTarget': 'crate::StartHookTarget', 'StopTarget': 'crate::StopHookTarget', 'ModelRequestStatus': 'crate::ModelRequestStatus', 'ModelRequestEvent': 'crate::ModelRequestHookEvent', 'LegacyEvent': 'crate::HookEvent', 'OriginalRequest': 'crate::native_occurrence::NativeRequest'}
    def snake(name):
        return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
    def call(kind, value):
        row = types[kind]
        if row['kind'] == 'optional':
            return f'self.flag({value}.is_some())?; if let Some(item) = {value}.as_ref() {{ {call(row["item"], "item")} }}'
        if kind == 'Strings':
            return f'self.count({value}.len())?; for item in {value} {{ self.string(item)?; }}'
        if kind == 'Path':
            return f'self.path({value}.as_path())?;'
        if kind == 'I64':
            return f'self.put(&{value}.to_be_bytes())?;'
        if kind == 'Bool':
            return f'self.flag(*{value})?;'
        return f'self.{snake(kind)}({value})?;'
    out = ['// Generated from native_boundaries/native_message.toml. Private DATA encoder only.', f'const VERSION: u16 = {model["version"]};', f'const SOURCE_FEATURE: u16 = {model["source_feature"]};', "impl<'o, 's, 'v> Encoder<'o, 's, 'v> {"]
    for name, source in structs.items():
        fields = types[name]['fields']
        out += [f"fn {snake(name)}(&mut self, value: &'v {source}) -> Result<(), EncodeError> {{", f'let {source} {{ ' + ', '.join(f['name'] for f in fields) + ' } = value;']
        out += [call(f['type'], f['name']) for f in fields]
        out += ['Ok(())', '}']
    for name, source in unions.items():
        out += [f"fn {snake(name)}(&mut self, value: &'v {source}) -> Result<(), EncodeError> {{", 'match value {']
        for case in types[name]['cases']:
            item = case['type']
            variant = source+'::'+case['name']
            if name == 'OriginalRequest':
                pattern = variant+'(item)'
                actions = call(item, 'item')
            elif name == 'LegacyEvent':
                pattern = variant+' { event }'
                actions = call(item, 'event')
            elif item == 'Unit':
                pattern = variant
                actions = ''
            else:
                fields = types[item]['fields']
                pattern = variant+' { '+', '.join(f['name'] for f in fields)+' }'
                actions = ' '.join(call(f['type'], f['name']) for f in fields)
            out += [f'{pattern} => {{ self.tag({case["tag"]})?; {actions} }}']
        if name == 'OriginalRequest':
            out += ['#[cfg(test)] crate::native_occurrence::NativeRequest::Test(_) => return Err(EncodeError::Unsupported),']
        out += ['}', 'Ok(())', '}']
    envelope = {
        "Version": "self.tag(VERSION)?;", "SourceFeature": "self.tag(SOURCE_FEATURE)?;",
        "Platform": "self.put(&[if cfg!(unix) {1} else if cfg!(windows) {2} else {return Err(EncodeError::Unsupported)}])?;",
        "Launch": "self.put(&occurrence.id().canonical_launch_id())?;",
        "Occurrence": "self.put(&occurrence.id().canonical_event_id())?;",
        "Instance": "self.put(&occurrence.native_session_instance().canonical_event_id())?;",
        "CapturedKind": "self.tag(captured_kind(kind))?;", "ActorContext": "self.context(context)?;",
        "OriginalRequest": "self.original_request(invocation.original())?;",
    }
    out += ["fn invocation(&mut self, invocation: &'v std::sync::Arc<crate::NativeInvocation>) -> Result<(), EncodeError> {",
            "let occurrence=invocation.occurrence();",
            "let crate::NativeOccurrenceKind::Invocation(kind)=occurrence.kind() else { return Err(EncodeError::Context); };",
            "let context=occurrence.actor_context().ok_or(EncodeError::Context)?;",
            "if occurrence.native_session_instance()!=context.actor() { return Err(EncodeError::Context); }"]
    out += [envelope[field] for field in model["envelope"]]
    out += ['Ok(())', '}', '}', 'fn captured_kind(value: crate::NativeHookKind) -> u16 {', 'match value {']
    for row in model['event_kind']:
        pattern='crate::NativeHookKind::LegacyAfterAgent' if row['name']=='LegacyAfterAgent' else 'crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::'+row['name']+')'
        out += [f'{pattern} => {row["tag"]},']
    out += ['}', '}']
    out += ['fn value_tag(value: &serde_json::Value) -> u16 {', 'match value {']
    for case in types['Value']['cases']:
        pattern = 'serde_json::Value::'+case['name']+('' if case['name']=='Null' else '(_)')
        out += [f'{pattern} => {case["tag"]},']
    out += ['}', '}']
    return '\n'.join(out)+'\n'
