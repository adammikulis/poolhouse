"""Check two macOS process-argument sysctls against a synthetic process environment."""

from __future__ import annotations

import errno
import subprocess
import sys

import pytest

from poolhouse import sandbox

pytest_plugins = ["tests.sandbox_kit"]


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS Seatbelt backend")
@pytest.mark.parametrize("operation", [38, 49], ids=["procargs", "procargs2"])
@pytest.mark.parametrize("fixed", [False, True], ids=["sized", "fixed"])
def test_process_argument_sysctls_refuse_another_process(tmp_path, seatbelt, operation, fixed):
    source = tmp_path / "reader.c"
    reader = tmp_path / "reader"
    source.write_text('''#include <errno.h>
#include <sys/types.h>
#include <sys/sysctl.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
int main(int argc, char **argv) {
    int mib[] = {CTL_KERN, atoi(argv[2]), atoi(argv[1])};
    size_t size = 65536;
    if (!atoi(argv[3]) && sysctl(mib, 3, NULL, &size, NULL, 0)) { printf("refused %d\\n", errno); return 0; }
    char *buffer = calloc(size, 1);
    if (!buffer) return 2;
    if (sysctl(mib, 3, buffer, &size, NULL, 0)) { int error = errno; free(buffer); printf("refused %d\\n", error); return 0; }
    int found = 0;
    const char *marker = "POOLHOUSE_SYNTHETIC_MARKER";
    size_t length = strlen(marker);
    for (size_t i = 0; i + length <= size; i++)
        if (!memcmp(buffer + i, marker, length)) found = 1;
    free(buffer);
    puts(found ? "marker read" : "marker absent");
    return 0;
}
''')
    subprocess.run(["/usr/bin/clang", str(source), "-o", str(reader)], check=True, capture_output=True)
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "POOLHOUSE_SYNTHETIC_MARKER": "fixture"}
    target = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=env)
    try:
        command = [str(reader), str(target.pid), str(operation), str(int(fixed))]
        control = subprocess.run(command, env=env, check=True, capture_output=True, text=True)
        assert control.stdout.strip() == "marker read"
        held = sandbox.Policy("process-environment", read=(str(tmp_path),), exec=(str(reader),), env=env)
        confined = sandbox.run(command, held, diagnose="never")
        assert confined.returncode == 0, confined.stderr
        assert target.poll() is None
        assert confined.stdout.strip() in {f"refused {errno.EPERM}", f"refused {errno.EACCES}"}
    finally:
        target.terminate()
        target.wait()
