"""Hostile canonical data and real language layouts; never invokes service observation."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from node_contract import passive_ffi, service_lifecycle  # noqa: E402
from node_contract.load import load_with_ownership  # noqa: E402


@pytest.fixture
def ffi():
    meta, _, _, _ = load_with_ownership(ROOT / 'contracts/node')
    return meta['service_lifecycle']['passive']['ffi']


@pytest.mark.parametrize('attack', ['version', 'layout', 'bool-offset', 'function', 'extra-field',
                                  'bool-code', 'negative', 'overflow', 'duplicate-code',
                                  'duplicate-name', 'authority-tag', 'code-injection',
                                  'message-control', 'message-oversize', 'missing-clock'])
def test_passive_abi_refuses_malformed_source(ffi, attack):
    data = copy.deepcopy(ffi)
    if attack == 'version':
        data['version'] = 2
    elif attack == 'layout':
        data['fields'][1]['offset'] = 4
    elif attack == 'bool-offset':
        data['fields'][0]['offset'] = False
    elif attack == 'function':
        data['function'] = 'arbitrary_service'
    elif attack == 'extra-field':
        data['principal'] = 'trusted'
    elif attack == 'bool-code':
        data['availability'][0]['code'] = False
    elif attack == 'negative':
        data['availability'][0]['code'] = -1
    elif attack == 'overflow':
        data['availability'][0]['code'] = 1 << 32
    elif attack == 'duplicate-code':
        data['reasons'][1]['code'] = data['reasons'][0]['code']
    elif attack == 'duplicate-name':
        data['reasons'][1]['name'] = data['reasons'][0]['name']
    elif attack == 'authority-tag':
        data['availability'][0]['name'] = 'VerifiedInstallation'
    elif attack == 'code-injection':
        data['reasons'][1]['message'] = '\\(execute())'
    elif attack == 'message-control':
        data['reasons'][1]['message'] = 'line\nline'
    elif attack == 'message-oversize':
        data['reasons'][1]['message'] = 'x' * 2049
    else:
        data['reasons'] = [row for row in data['reasons'] if row['name'] != 'ClockUnavailable']
    with pytest.raises(ValueError):
        passive_ffi.validate(data, service_lifecycle.OBSERVATIONS['PassiveServiceAvailability'])


def test_generated_rust_layout_and_refusal(tmp_path, ffi):
    from native_cargo import ACCOUNT, RUN_S, testnode

    source = tmp_path / 'passive.rs'
    source.write_text(passive_ffi.render_rust(ffi) + '''
fn main() {
    let raw = |t,a,r| PassiveServiceObservationV1 { observed_at:t, availability:a, reason:r };
    let valid = PassiveServiceData::decode(0,raw(1,5,11)).unwrap();
    assert_eq!(valid.availability,PassiveServiceAvailabilityTag::RegisteredEligible);
    assert_eq!(valid.observed_at,1);
    assert_eq!(valid.reason.message(),"Registration eligibility only; installation and runtime readiness are unverified.");
    assert!(PassiveServiceData::decode(0,raw(0,0,13)).is_some());
    for (result,t,a,r) in [(1,1,5,11),(99,1,5,11),(0,1,u32::MAX,11),(0,1,5,u32::MAX),
                           (0,0,5,13),(0,0,0,0),(0,1,0,13)] {
        assert!(PassiveServiceData::decode(result,raw(t,a,r)).is_none());
    }
}
''')
    environment = {**os.environ, 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path),
                   'RUSTUP_HOME': str(ACCOUNT / '.rustup'), 'CARGO_HOME': str(ACCOUNT / '.cargo')}
    binary = tmp_path / 'passive-rust'
    built = subprocess.run([testnode.tool('rustc'), '--edition=2021', str(source), '-o', str(binary)],
                           capture_output=True, text=True, env=environment, timeout=RUN_S)
    assert built.returncode == 0, built.stderr
    subprocess.run([str(binary)], check=True, capture_output=True, env=environment, timeout=10)


@pytest.mark.skipif(sys.platform != 'darwin', reason='actual macOS public SDK')
def test_generated_c_swift_layout_and_refusal(tmp_path, ffi):
    header = tmp_path / 'PassiveServiceData.h'
    header.write_text(passive_ffi.render_header(ffi))
    c_source = tmp_path / 'layout.c'
    c_source.write_text('#include "PassiveServiceData.h"\n')
    environment = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path)}
    subprocess.run(['/usr/bin/clang', '-std=c11', '-Werror', '-fsyntax-only', str(c_source)],
                   check=True, capture_output=True, env=environment, timeout=60)
    swift = tmp_path / 'PassiveServiceData.swift'
    swift.write_text(passive_ffi.render_swift(ffi))
    main = tmp_path / 'main.swift'
    main.write_text('''
import Foundation
func raw(_ t: UInt64,_ a: UInt32,_ r: UInt32) -> PhPassiveServiceObservationV1 {
    PhPassiveServiceObservationV1(observed_at:t,availability:a,reason:r)
}
precondition(PassiveServiceData.validLayout())
precondition(PassiveServiceData.decode(result:0,raw:raw(1,5,11))?.availability == .registeredEligible)
precondition(PassiveServiceData.decode(result:0,raw:raw(0,0,13))?.reason == .clockUnavailable)
let refused: [(Int32,UInt64,UInt32,UInt32)] = [(1,1,5,11),(99,1,5,11),(0,1,UInt32.max,11),
    (0,1,5,UInt32.max),(0,0,5,13),(0,0,0,0),(0,1,0,13)]
for (result,t,a,r) in refused {
    precondition(PassiveServiceData.decode(result:result,raw:raw(t,a,r)) == nil)
}
''')
    binary = tmp_path / 'passive-swift'
    built = subprocess.run(['/usr/bin/swiftc', '-j', '1', '-module-cache-path', str(tmp_path / 'modules'),
                            '-import-objc-header', str(header), str(swift), str(main), '-o', str(binary)],
                           capture_output=True, text=True, env=environment, timeout=120)
    assert built.returncode == 0, built.stderr
    subprocess.run([str(binary)], check=True, capture_output=True, env=environment, timeout=10)
