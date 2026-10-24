"""SDK compilation and attribute-format refusals only, without native keys or prompts."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app/native-provider"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS public SDK compile")
def test_public_sdk_observer_and_attribute_refusals_without_keys(tmp_path):
    environment = dict(os.environ, HOME=str(tmp_path / "home"), TMPDIR=str(tmp_path))
    (tmp_path / "home").mkdir()
    output = tmp_path / "provider-format-tests"
    command = ["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-D", "TESTING",
               "-module-cache-path", str(tmp_path / "modules"), "-o", str(output),
               str(SOURCE / "KeyObservation.swift"), str(SOURCE / "KeyObservationTests.swift")]
    compiled = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(output)], env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "no key or policy admission" in result.stdout


    for attempt in (
        "func forge(_ c: ProviderKeyCustody) -> NativeKeyObservation { NativeKeyObservation(custody:c,publicKeyX963:Data()) }",
        "let forged = ProviderKeyPolicyObservation.osPerSignaturePresence",
    ):
        probe = tmp_path / "Forbidden.swift"
        probe.write_text("import Foundation\nimport Security\n" + attempt + "\n")
        denied = subprocess.run(["/usr/bin/swiftc", "-j", "1", "-typecheck", "-module-cache-path", str(tmp_path / "modules"),
                                 str(SOURCE / "KeyObservation.swift"), str(probe)],
                                env=environment, capture_output=True, text=True, check=False)
        assert denied.returncode != 0
        assert "protection level" in denied.stderr or "has no member" in denied.stderr, denied.stderr
