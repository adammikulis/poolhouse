"""`LlamaServerBackend` for a caller with no pool behind it (`poolhouse.core`): nothing is taken on trust that it can check.

* The binary is found once per start and that one path is used for the flag check, the preflight, the environment and
  the argv; whichever route found it (an explicit path, an environment variable, PATH) is judged by the policy.
* The policy hashes it again the instant before it is executed (`Policy.before_launch`, called in `launch`).
* The API key goes to the server in a file of mode 0600 (`--api-key-file`), not on a command line any process can read.
"""

from __future__ import annotations

from pathlib import Path

from poolhouse import serverkeys
from poolhouse.serve.backend import Lease, LlamaServerBackend, ServerInfo, ServerSpec
from poolhouse.serve.binary import require_binary

__all__ = ["StrictBackend"]


class StrictBackend(LlamaServerBackend):
    """The llama.cpp backend, judging every route to the binary and keeping the key off the command line."""

    _pinned: Path | None = None

    @property
    def binary(self) -> Path:
        return self._pinned or require_binary("llama-server", explicit=self._explicit, vendor_dir=self._vendor_dir,
                                              build=self._build, policy=self.policy, strict=True)

    def start(self, spec: ServerSpec, *, lease: Lease, timeout: float = 300.0, **starting: bool) -> ServerInfo:
        self._pinned = None
        self._pinned = self.binary
        try:
            return super().start(spec, lease=lease, timeout=timeout, **starting)
        finally:
            self._pinned = None

    def command(self, spec: ServerSpec) -> list[str]:
        argv = super().command(spec)
        if spec.api_key and "--api-key" in argv:
            at = argv.index("--api-key")
            argv[at:at + 2] = ["--api-key-file", str(serverkeys.write_key_file(spec.port, spec.api_key))]
        return argv
