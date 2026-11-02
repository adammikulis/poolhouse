"""Generate only passive in-process data; no service scope, issuer, IPC or actions."""
from __future__ import annotations

import json
import re
from pathlib import Path

LAYOUT = {'version': 1, 'function': 'ph_passive_service_observe_v1',
          'representation': 'in-process-untrusted-data-only', 'size': 16, 'alignment': 8,
          'fields': [{'name': 'observed_at', 'type': 'u64', 'offset': 0},
                     {'name': 'availability', 'type': 'u32', 'offset': 8},
                     {'name': 'reason', 'type': 'u32', 'offset': 12}],
          'results': [{'name': 'Ok', 'code': 0}, {'name': 'InvalidOutput', 'code': 1}]}


def _entries(rows, *, messages=False):
    if not isinstance(rows, list) or not rows:
        raise ValueError('passive ABI requires finite explicit entries')
    names, codes = set(), set()
    for row in rows:
        keys = {'name', 'code', 'message'} if messages else {'name', 'code'}
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError('unsupported passive ABI entry')
        name, code = row['name'], row['code']
        if not isinstance(name, str) or not re.fullmatch('[A-Z][A-Za-z0-9]*', name):
            raise ValueError('passive ABI names must be literal identifiers')
        if type(code) is not int or not 0 <= code <= 0xffffffff or name in names or code in codes:
            raise ValueError('passive ABI codes must be unique unsigned integers')
        names.add(name)
        codes.add(code)
        if messages:
            message = row['message']
            # Reuse the canonical SafeMessage ceiling; these are fixed literals, never format templates.
            if (not isinstance(message, str) or not 1 <= len(message) <= 2048
                    or any(ord(char) < 32 or ord(char) > 126 or char in '\\"' for char in message)):
                raise ValueError('passive reasons must be bounded plain ASCII literals')
    return names


def validate(source: dict, availability: list[str]) -> None:
    if not isinstance(source, dict) or set(source) != {*LAYOUT, 'availability', 'reasons'}:
        raise ValueError('unsupported passive ABI fields')
    # JSON comparison distinguishes booleans/floats from integer layout constants.
    if json.dumps({key: source[key] for key in LAYOUT}, sort_keys=True) != json.dumps(LAYOUT, sort_keys=True):
        raise ValueError('unsupported passive ABI version, function or layout')
    if _entries(source['availability']) != set(availability):
        raise ValueError('passive ABI availability must match the canonical data enum')
    reasons = _entries(source['reasons'], messages=True)
    if 'ClockUnavailable' not in reasons:
        raise ValueError('passive ABI requires the unavailable-clock reason')
    for table in ('availability', 'reasons'):
        if not any(row['name'] == 'Unknown' and row['code'] == 0 for row in source[table]):
            raise ValueError('passive ABI requires the explicit zero Unknown tag')


def _macro(name: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', name).upper()


def render_header(source: dict) -> str:
    lines = ['// Generated passive data only. No installation, scope or authority.',
             '#ifndef POOLHOUSE_PASSIVE_SERVICE_DATA_H', '#define POOLHOUSE_PASSIVE_SERVICE_DATA_H',
             '#include <stdint.h>', '#include <stddef.h>',
             '#define PH_PASSIVE_ABI_VERSION UINT32_C(1)']
    for table, prefix in [('results', 'RESULT'), ('availability', 'AVAILABILITY'), ('reasons', 'REASON')]:
        lines += [f'#define PH_PASSIVE_{prefix}_{_macro(row["name"])} {"INT32_C" if table == "results" else "UINT32_C"}({row["code"]})'
                  for row in source[table]]
    lines += ['typedef struct {', '    uint64_t observed_at;', '    uint32_t availability;',
              '    uint32_t reason;', '} PhPassiveServiceObservationV1;', '#ifdef __cplusplus',
              '#define PH_PASSIVE_ASSERT static_assert', '#define PH_PASSIVE_ALIGN alignof', 'extern "C" {',
              '#else', '#define PH_PASSIVE_ASSERT _Static_assert', '#define PH_PASSIVE_ALIGN _Alignof', '#endif',
              'PH_PASSIVE_ASSERT(sizeof(PhPassiveServiceObservationV1) == 16, "passive size");',
              'PH_PASSIVE_ASSERT(PH_PASSIVE_ALIGN(PhPassiveServiceObservationV1) == 8, "passive alignment");']
    lines += [f'PH_PASSIVE_ASSERT(offsetof(PhPassiveServiceObservationV1, {row["name"]}) == {row["offset"]}, "passive offset");'
              for row in source['fields']]
    lines += ['// Trusted in-process caller supplies valid writable storage of this exact type.',
              '// Null returns InvalidOutput without writing; Ok fully initializes every field.',
              f'int32_t {source["function"]}(PhPassiveServiceObservationV1 *output);',
              '#ifdef __cplusplus', '}', '#endif', '#undef PH_PASSIVE_ASSERT', '#undef PH_PASSIVE_ALIGN', '#endif', '']
    return '\n'.join(lines)


def render_swift(source: dict) -> str:
    lines = ['// Generated passive data only; no installation or authority.', 'import Foundation']
    for table, name in [('results', 'PassiveServiceResult'), ('availability', 'PassiveServiceAvailabilityTag'),
                        ('reasons', 'PassiveServiceReasonCode')]:
        lines += [f'enum {name}: {"Int32" if table == "results" else "UInt32"} {{']
        lines += [f'    case {row["name"][0].lower() + row["name"][1:]} = {row["code"]}' for row in source[table]]
        if table == 'reasons':
            lines += ['    var message: String {', '        switch self {']
            lines += [f'        case .{row["name"][0].lower() + row["name"][1:]}: return {json.dumps(row["message"])}'
                      for row in source[table]]
            lines += ['        }', '    }']
        lines += ['}']
    lines += ['struct PassiveServiceData {', '    let observedAt: UInt64',
              '    let availability: PassiveServiceAvailabilityTag', '    let reason: PassiveServiceReasonCode',
              '    static func validLayout() -> Bool {',
              '        MemoryLayout<PhPassiveServiceObservationV1>.size == 16',
              '            && MemoryLayout<PhPassiveServiceObservationV1>.stride == 16',
              '            && MemoryLayout<PhPassiveServiceObservationV1>.alignment == 8']
    lines += [f'            && MemoryLayout<PhPassiveServiceObservationV1>.offset(of: \\.{row["name"]}) == {row["offset"]}'
              for row in source['fields']]
    lines += ['    }', '    static func decode(result: Int32, raw: PhPassiveServiceObservationV1) -> Self? {',
              '        guard validLayout(), result == PassiveServiceResult.ok.rawValue,',
              '              let availability = PassiveServiceAvailabilityTag(rawValue: raw.availability),',
              '              let reason = PassiveServiceReasonCode(rawValue: raw.reason) else { return nil }',
              '        if raw.observed_at == 0 || reason == .clockUnavailable {',
              '            guard raw.observed_at == 0, availability == .unknown, reason == .clockUnavailable else { return nil }',
              '        }', '        return Self(observedAt: raw.observed_at, availability: availability, reason: reason)',
              '    }', '}', '']
    return '\n'.join(lines)


def render_rust(source: dict) -> str:
    lines = ['// Generated app-private passive data. No authority or serialization traits.',
             '#[repr(C)] #[derive(Clone, Copy)]', 'pub(crate) struct PassiveServiceObservationV1 {',
             '    pub(crate) observed_at: u64,', '    pub(crate) availability: u32,', '    pub(crate) reason: u32,', '}',
             'const _: () = {', '    assert!(std::mem::size_of::<PassiveServiceObservationV1>() == 16);',
             '    assert!(std::mem::align_of::<PassiveServiceObservationV1>() == 8);']
    lines += [f'    assert!(std::mem::offset_of!(PassiveServiceObservationV1, {row["name"]}) == {row["offset"]});'
              for row in source['fields']]
    lines += ['};']
    for table, name, integer in [('results', 'PassiveServiceResult', 'i32'),
                                 ('availability', 'PassiveServiceAvailabilityTag', 'u32'),
                                 ('reasons', 'PassiveServiceReasonCode', 'u32')]:
        lines += ['#[derive(Clone, Copy, Debug, PartialEq, Eq)]', f'pub(crate) enum {name} {{']
        lines += [f'    {row["name"]},' for row in source[table]]
        lines += ['}', f'impl {name} {{', f'    pub(crate) fn from_raw(value: {integer}) -> Option<Self> {{', '        match value {']
        lines += [f'            {row["code"]} => Some(Self::{row["name"]}),' for row in source[table]]
        lines += ['            _ => None,', '        }', '    }']
        if table == 'reasons':
            lines += ["    pub(crate) fn message(self) -> &'static str {", '        match self {']
            lines += [f'            Self::{row["name"]} => {json.dumps(row["message"])},' for row in source[table]]
            lines += ['        }', '    }']
        lines += ['}']
    lines += ['pub(crate) struct PassiveServiceData {', '    pub(crate) observed_at: u64,',
              '    pub(crate) availability: PassiveServiceAvailabilityTag,', '    pub(crate) reason: PassiveServiceReasonCode,', '}',
              'impl PassiveServiceData {', '    pub(crate) fn decode(result: i32, raw: PassiveServiceObservationV1) -> Option<Self> {',
              '        if PassiveServiceResult::from_raw(result)? != PassiveServiceResult::Ok { return None; }',
              '        let availability = PassiveServiceAvailabilityTag::from_raw(raw.availability)?;',
              '        let reason = PassiveServiceReasonCode::from_raw(raw.reason)?;',
              '        if (raw.observed_at == 0 || reason == PassiveServiceReasonCode::ClockUnavailable)',
              '            && !(raw.observed_at == 0 && availability == PassiveServiceAvailabilityTag::Unknown',
              '                && reason == PassiveServiceReasonCode::ClockUnavailable) { return None; }',
              '        Some(Self { observed_at: raw.observed_at, availability, reason })', '    }', '}', '']
    return '\n'.join(lines)


def outputs(root: Path, source: dict) -> dict[Path, str]:
    return {root / 'app/native-service/generated/PassiveServiceData.h': render_header(source),
            root / 'app/native-service/generated/PassiveServiceData.swift': render_swift(source),
            root / 'app/src-tauri/src/generated/passive_service.rs': render_rust(source)}
