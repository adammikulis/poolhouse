"""Public SDK compile and genuine outside-app rejection; no installed service is manufactured."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ServiceManagement SDK")
def test_passive_service_rejects_outside_containing_app(tmp_path):
    source = Path(__file__).resolve().parents[1] / "app/native-service"
    binary = tmp_path / "inspection"
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path),
                   "TMPDIR": str(tmp_path), "LANG": "en_US.UTF-8"}
    compiled = subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library",
                    "-D", "SERVICE_INSPECTION_VERIFY", "-module-cache-path", str(tmp_path / "modules"),
                    str(source / "Inspection.swift"), str(source / "InspectionVerification.swift"),
                    "-framework", "Security", "-framework", "ServiceManagement", "-o", str(binary)],
                   check=False, env=environment, timeout=120, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    completed = subprocess.run([str(binary)], check=True, env=environment, timeout=10,
                               capture_output=True, text=True)
    assert completed.stdout == "PASS: outside designated app; BindingUnavailable; no service registration query\n"
    assert completed.stderr == ""
