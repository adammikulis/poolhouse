"""The Docker side of scripts/pool-check: the image, the network and the Linux devices, all labelled so one run removes only its own.

Nothing here touches anything it did not make: every container and network carries the label ``poolhouse.pool-check=RUN``
and the image is tagged by the hash of its build context, so a second run with the same tree reuses it and `--clean`
removes it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABEL = "poolhouse.pool-check"
IMAGE = "poolhouse-pool-check"
PORT = 7447
KEEP = ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "contracts/", "patches/", "src/", "scripts/",
        "app/Cargo.toml", "app/Cargo.lock", "app/poolhouse-node/", "app/src-tauri/Cargo.toml", "app/src-tauri/build.rs",
        "app/src-tauri/src/", "app/src-tauri/tauri.conf.json", "app/src-tauri/capabilities/", "app/src-tauri/icons/")


class DockerError(RuntimeError):
    """Docker refused, or is not running; the message carries its own output."""


def account_environment() -> dict[str, str]:
    """This process's environment with HOME as the account has it: the docker command finds its context and BuildKit setting there, and a test
    run moves HOME to a temporary folder (the pool check's own devices never use the account's home: their states are under /tmp)."""
    env = dict(os.environ)
    if os.name == "posix":
        import pwd

        env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    env["DOCKER_BUILDKIT"] = "1"
    return env


def docker(*args: str, check: bool = True, timeout: float = 600, input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``docker ARGS``; on a failure (with ``check``) raise with the command and everything it printed."""
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False, input=input, env=account_environment())
    if check and done.returncode:
        raise DockerError(f"docker {' '.join(args)} exited {done.returncode}:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")
    return done


def available() -> str:
    """The reason Docker cannot be used, or an empty string when it answers."""
    try:
        done = docker("info", "--format", "{{.OperatingSystem}}", check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker is not usable: {exc}"
    return "" if not done.returncode else f"the Docker daemon is not running: {done.stderr.strip()[-300:]}"


def tracked_files() -> list[str]:
    """The working tree's files, uncommitted edits and new files included, that the image needs."""
    done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True)
    names = [n for n in done.stdout.split("\0") if n and n.startswith(KEEP) and (ROOT / n).is_file()]
    return sorted(names)


def context_hash(names: list[str]) -> str:
    """A short hash of the names and contents of the build context: what tags the image."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def stage_context(into: Path, names: list[str]) -> None:
    """Copy the files to ``into`` (symlinks as files) with the Dockerfile and its ignore list beside them."""
    for name in names:
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copy2(Path(__file__).with_name("Dockerfile"), into / "Dockerfile")


DISTRO = "ubuntu:24.04"


def build_image(distro: str = DISTRO) -> str:
    """The image tag of this tree on ``distro``, built when it is not there yet (BuildKit caches the cargo registry, the target folder and uv)."""
    names = tracked_files()
    slug = "".join(c if c.isalnum() else "-" for c in distro)
    tag = f"{IMAGE}:{context_hash(names)}-{slug}"
    if docker("image", "inspect", tag, check=False).returncode == 0:
        return tag
    with tempfile.TemporaryDirectory(prefix="pcctx") as temporary:
        stage_context(Path(temporary), names)
        docker("build", "--label", f"{LABEL}=image", "--build-arg", f"DISTRO={distro}", "-t", tag, temporary, timeout=3600)
    return tag


def remove_images() -> None:
    """Remove every image this rig built."""
    done = docker("images", "-q", "--filter", f"label={LABEL}", check=False)
    for image in sorted(set(done.stdout.split())):
        docker("image", "rm", "-f", image, check=False)


@dataclass
class Net:
    """A user-defined bridge network the Linux devices share; its name is the run's."""

    name: str
    run: str = ""
    nat: bool = False
    """A network a device sits behind NAT on: nothing is published and the other devices have no route in."""

    @classmethod
    def make(cls, run: str, suffix: str = "") -> Net:
        docker("network", "create", "--label", f"{LABEL}={run}", f"pc-{run}{suffix}")
        return cls(f"pc-{run}{suffix}", run, nat=bool(suffix))

    def remove(self) -> None:
        docker("network", "rm", self.name, check=False)


@dataclass
class Box:
    """One Linux container: a device with its own state directory, name, kernel namespace and address."""

    name: str
    port: int
    run: str

    @classmethod
    def start(cls, net: Net, image: str, index: int, port: int) -> Box:
        """Start it (idle) on the network. Its node port is published on this machine's loopback only, except for a device behind NAT:
        that one has a network of its own, nothing published and no route in from anywhere, and the capability to bind-mount the WSL markers."""
        run, nat = net.run, net.nat
        name = f"pc-{run}-{index}"
        ports = [] if nat else ["-p", f"127.0.0.1:{port}:{port}"]
        extra = ["--cap-add", "SYS_ADMIN", "-e", "WSL_DISTRO_NAME=Ubuntu"] if nat else []
        docker("run", "-d", "--name", name, "--hostname", f"{'wsl' if nat else 'linux'}{index}", "--network", net.name, "--label", f"{LABEL}={run}",
               *ports, *extra, "--add-host", "host.docker.internal:host-gateway", image, "sleep", "infinity")
        return cls(name, port, run)

    WSL_VERSION = "Linux version 5.15.153.1-microsoft-standard-WSL2 (root@build) #1 SMP Fri Mar 29 23:14:13 UTC 2024"

    def mark_as_wsl(self) -> None:
        """Make the kernel files production reads to detect WSL say so: /proc/version and /proc/sys/kernel/osrelease, bind-mounted from files
        of the container's own. `platform.release()` (the uname call) is the kernel's and cannot be changed from here."""
        self.exec("sh", "-c", f"echo '{self.WSL_VERSION}' > /pc-version && echo 5.15.153.1-microsoft-standard-WSL2 > /pc-osrelease"
                  " && mount --bind /pc-version /proc/version && mount --bind /pc-osrelease /proc/sys/kernel/osrelease"
                  " && cp /rig/pool_check/wsl_stubs/* /usr/local/bin/ && chmod +x /usr/local/bin/powershell.exe /usr/local/bin/wslpath /usr/local/bin/wsl.exe"
                  " && mkdir -p /mnt/c/Users/poolcheck")

    def exec(self, *argv: str, check: bool = True, timeout: float = 120, env: dict[str, str] | None = None,
             input: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run a command inside, as the device's user."""
        flags = [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
        return docker("exec", *(["-i"] if input is not None else []), *flags, self.name, *argv, check=check, timeout=timeout, input=input)

    def put(self, source: Path, target: str) -> None:
        """Copy a folder or file of this machine to ``target`` inside (the rig's own helpers, not part of the image)."""
        self.exec("mkdir", "-p", target)
        docker("cp", str(source), f"{self.name}:{target}/")

    def ip(self, net: Net) -> str:
        """Its address on the network."""
        fmt = "{{(index .NetworkSettings.Networks \"" + net.name + "\").IPAddress}}"
        return docker("inspect", "-f", fmt, self.name).stdout.strip()

    def disconnect(self, net: Net) -> None:
        """Cut it off from the network (a partition)."""
        docker("network", "disconnect", net.name, self.name)

    def connect(self, net: Net) -> None:
        """Put it back on the network."""
        docker("network", "connect", net.name, self.name)

    def remove(self) -> None:
        docker("rm", "-f", "-v", self.name, check=False)


def swept(run: str) -> list[str]:
    """What of this run still exists (containers, networks): empty after a clean exit."""
    left = docker("ps", "-aq", "--filter", f"label={LABEL}={run}", check=False).stdout.split()
    left += docker("network", "ls", "-q", "--filter", f"label={LABEL}={run}", check=False).stdout.split()
    return left


def wait_for(check, seconds: float, what: str, every: float = 0.25):
    """The first truthy ``check()`` within ``seconds``, else AssertionError naming what never happened."""
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = check()
        except (OSError, ValueError, DockerError, KeyError) as exc:
            last = None
            note = str(exc)
        else:
            note = ""
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {seconds:.0f}s waiting for {what}{': ' + note if note else ''}")
