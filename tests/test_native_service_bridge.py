"""Link the actual Swift bridge and Rust mapper, without launching the product app."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS public SDK')
def test_actual_swift_archive_links_to_checked_rust_mapper(tmp_path):
    from native_cargo import ACCOUNT, RUN_S, testnode

    crate = tmp_path / 'builder'
    (crate / 'src').mkdir(parents=True)
    (crate / 'Cargo.toml').write_text('[package]\nname="passive-bridge-build-check"\nversion="0.0.0"\nedition="2021"\n'
                                  '[dependencies]\nserde_json="1"\n')
    helper = ROOT / 'app/src-tauri/native_service_build.rs'
    (crate / 'src/main.rs').write_text(f'''#[path={json.dumps(str(helper), ensure_ascii=False)}] mod native_service_build;
fn main() {{
    std::env::set_var("CARGO_MANIFEST_DIR",{json.dumps(str(ROOT / 'app/src-tauri'), ensure_ascii=False)});
    std::env::set_var("OUT_DIR",std::env::args().nth(1).unwrap());
    std::env::set_var("CARGO_CFG_TARGET_ARCH",std::env::consts::ARCH);
    native_service_build::build();
}}
''')
    output = tmp_path / 'native'
    output.mkdir()
    environment = {**os.environ, 'HOME': str(tmp_path), 'TMPDIR': str(tmp_path),
                   'RUSTUP_HOME': str(ACCOUNT / '.rustup'), 'CARGO_HOME': str(ACCOUNT / '.cargo'),
                   'CARGO_TARGET_DIR': str(tmp_path / 'target'), 'CARGO_BUILD_JOBS': '1'}
    built = subprocess.run([testnode.tool('cargo'), 'run', '--offline', '--quiet',
                            '--manifest-path', str(crate / 'Cargo.toml'), '--', str(output)],
                           env=environment, capture_output=True, text=True, timeout=RUN_S)
    assert built.returncode == 0, built.stderr
    arguments = []
    for line in built.stdout.splitlines():
        if line.startswith('cargo:rustc-link-search='):
            arguments.extend(['-L', line.split('=', 1)[1]])
        elif line.startswith('cargo:rustc-link-lib='):
            arguments.extend(['-l', line.split('=', 1)[1]])
        elif line.startswith('cargo:rustc-link-arg='):
            arguments.extend(['-C', 'link-arg=' + line.split('=', 1)[1]])
    assert arguments, 'the production build helper must emit a real link plan'
    mapper = ROOT / 'app/src-tauri/src/service_availability.rs'
    source = tmp_path / 'bridge.rs'
    source.write_text(f'#[path={json.dumps(str(mapper), ensure_ascii=False)}] mod service_availability;\n')
    binary = tmp_path / 'bridge-verification'
    linked = subprocess.run([testnode.tool('rustc'), '--edition=2021', '--test', str(source),
                             '-o', str(binary), *arguments], env=environment,
                            capture_output=True, text=True, timeout=RUN_S)
    assert linked.returncode == 0, linked.stderr
    observed = subprocess.run([str(binary), '--exact',
                               'service_availability::tests::actual_swift_ffi_rejects_null_and_observes_outside_app'],
                              env=environment, capture_output=True, text=True, timeout=10)
    assert observed.returncode == 0, observed.stderr
    assert '1 passed; 0 failed' in observed.stdout
