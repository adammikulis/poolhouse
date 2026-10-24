"""Launch accounting DATA checks without native graph instances or OS execution."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import testnode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from node_contract import adapters, launch_data  # noqa: E402

SOURCE = ROOT / "contracts/node/native_boundaries/launch_data.toml"


def test_launch_layout_table_matches_canonical_definition():
    assert launch_data.render(launch_data.load(ROOT / "contracts/node")) == (ROOT / "app/poolhouse-node/src/contract_launch_data.rs").read_text()


@pytest.mark.parametrize("change", ["inherited-environment", "missing-argv0", "wrong-profile", "unversioned-source", "windows-fallback", "bool-count"])
def test_launch_grammar_refuses_ambient_unbound_and_unpinned_rules(tmp_path, change):
    text = SOURCE.read_text()
    old, new = {
        "inherited-environment": ('environment = "explicit-ordered-named-entries-no-inheritance"', 'environment = "inherit-current-process"'),
        "missing-argv0": ('arguments = "complete-argv-including-explicit-argv0"', 'arguments = "tail-only"'),
        "wrong-profile": ('profile = "MacNativeImage"', 'profile = "LinuxNativeImage"'),
        "unversioned-source": ('source_revision = "ac9718fb1af618d5ce8678d0dc6e8a58f252216f"', 'source_revision = "main"'),
        "windows-fallback": ('profile = "WindowsNativeUnavailable"', 'profile = "GenericCrt"'),
        "bool-count": ('input_vector_null_slots = 2', 'input_vector_null_slots = true'),
    }[change]
    assert old in text
    folder = tmp_path / "native_boundaries"
    folder.mkdir()
    (folder / "launch_data.toml").write_text(text.replace(old, new, 1))
    with pytest.raises(ValueError):
        launch_data.load(tmp_path)


@pytest.fixture(scope="module")
def launch_probe(tmp_path_factory):
    work = tmp_path_factory.mktemp("launch-data")
    source = '''#![allow(dead_code)]
// Type-compatibility data declarations only; no graph implementations or issuers.
mod contract {
    #[derive(Debug)] pub struct InstallationId(String);
    impl InstallationId { pub fn as_str(&self)->&str { &self.0 } }
    #[derive(Debug)] pub struct ResourceGeneration(String);
    impl ResourceGeneration { pub fn as_str(&self)->&str { &self.0 } }
    #[derive(Debug)] pub struct WindowsSid;
    #[derive(Debug)] pub struct AuditToken;
    pub struct ProcessObservation;
}
mod contract_lifecycle {
    use super::contract::ProcessObservation;
    #[derive(Debug)] pub enum NativeAdapterError { Io }
''' + adapters.render(adapters.load(ROOT / "contracts/node")) + '\n}\n'
    path = ROOT / "app/poolhouse-node/src/lifecycle/launch_data.rs"
    source += '#[path = ' + json.dumps(str(path), ensure_ascii=False) + '] mod launch_data;\n'
    (work / "main.rs").write_text(source)
    account = testnode.account()
    environment = {**os.environ, "HOME": str(work / "home"), "POOLHOUSE_HOME": str(work / "state"),
                   "TMPDIR": str(work), "RUSTUP_HOME": str(account / ".rustup"), "CARGO_HOME": str(account / ".cargo")}
    (work / "home").mkdir()
    compiled = subprocess.run([testnode.tool("rustc"), "--edition=2021", "--test", str(work / "main.rs"), "-o", str(work / "probe")],
                              cwd=work, env=environment, capture_output=True, text=True, check=False)
    assert compiled.returncode == 0, compiled.stderr
    return work, environment


def test_immutable_launch_data_binding_and_accounting(launch_probe):
    work, environment = launch_probe
    result = subprocess.run([str(work / "probe"), "launch_data::tests::", "--test-threads=1"],
                            cwd=work, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test result: ok. 6 passed; 0 failed;" in result.stdout, result.stdout
