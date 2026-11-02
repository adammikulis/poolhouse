"""Every Poolhouse process of an installed Mac runs as an executable named Poolhouse, in a signed bundle of its own.

macOS asks about a program, and keeps the answer, by the executable it runs: its path and its code signature. The firewall list and
the Local Network prompt therefore said python3.13 for the daemon, and one Allow belonged to every program using that Python. These
tests build the bundle for real (a copy of the interpreter, signed with `codesign`, ad hoc here because a test never touches the
keychain that holds the local certificate), run a runtime through it and ask the kernel which executable the process is.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from poolhouse import (
    app_bundle,
    macho,
    node_app,
    node_signing,
    platform as poolhouse_platform,
    real_machine,
    runtime,
    runtime_deploy,
    runtime_host,
    runtime_identity,
    setup,
)
from poolhouse.home import home

SHA1 = "4E2F3980AC138E86B7532FF4E3460EF53D0E7B23"
COMMIT = "a" * 40
NOWHERE = runtime_deploy.Plan(Path("/nowhere"), COMMIT, None)


def load_command(name: str) -> bytes:
    raw = name.encode() + b"\0"
    size = (24 + len(raw) + 7) // 8 * 8
    return struct.pack("<IIIIII", 0x0C, size, 24, 0, 0, 0) + raw.ljust(size - 24, b"\0")


def thin(*names: str, cpu: int = 0x0100000C) -> bytes:
    commands = b"".join(load_command(name) for name in names)
    return struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 0, 2, len(names), len(commands), 0, 0) + commands


def fat(*slices: tuple[int, bytes]) -> bytes:
    table, body, offset = b"", b"", 8 + 20 * len(slices)
    for cpu, data in slices:
        table += struct.pack(">iiIII", cpu, 0, offset + len(body), len(data), 14)
        body += data
    return struct.pack(">II", 0xCAFEBABE, len(slices)) + table + body


@pytest.fixture
def macos():
    """The test needs macOS: the kernel there names a program by its executable and signature."""
    if sys.platform != "darwin":
        pytest.skip("macOS names a program by its executable and signature")


@pytest.fixture(autouse=True)
def no_icon(monkeypatch):
    """A bundle in a test has no icon: drawing one starts the system's renderer."""
    monkeypatch.setattr(app_bundle, "ICON_SVG", Path("/nowhere/Poolhouse.svg"))


@pytest.fixture
def ad_hoc(monkeypatch):
    """Sign the way a test may: ad hoc, with a requirement that names only the id (the certificate is the installed software's)."""
    def sign(bundle, identifier, requirement_for):
        done = subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", identifier, "--timestamp=none",
                               f'-r=designated => identifier "{identifier}"', str(bundle)], capture_output=True, text=True, check=False)
        if done.returncode:
            raise node_signing.SigningError(done.stderr)
        return "ad-hoc"

    monkeypatch.setattr(runtime_identity, "stable_requirement", lambda text: f'identifier "{runtime_identity.BUNDLE_ID}"' in text)
    return sign


def make_runtime(tmp_path: Path, name: str = "one") -> runtime.Runtime:
    prefix = tmp_path / "runtimes" / name
    subprocess.run([runtime_identity.unbound(Path(sys.executable)), "-m", "venv", "--without-pip", str(prefix)], check=True, capture_output=True)
    return runtime.Runtime(prefix, COMMIT, "0", runtime.identity())


def test_the_identity_is_poolhouse_with_an_id_of_its_own_beside_the_nodes():
    assert runtime_identity.BUNDLE_ID == "app.poolhouse.runtime" != node_app.BUNDLE_ID  # a new id is a new app to macOS
    path = runtime_identity.executable_path("darwin-arm64-cpython-313")
    assert path.name == "Poolhouse" and path.parts[-5:-2] == ("darwin-arm64-cpython-313", "Poolhouse.app", "Contents")
    assert path.parts[-6] == "runtime" and path.parts[-7] == "apps"


def test_the_bundle_describes_itself_as_poolhouse():
    plist = runtime_identity.info_plist("abc123")
    assert plist["CFBundleIdentifier"] == "app.poolhouse.runtime" and plist["CFBundleExecutable"] == "Poolhouse"
    assert plist["CFBundleName"] == plist["CFBundleDisplayName"] == "Poolhouse" and plist["LSUIElement"] is True
    assert plist["NSLocalNetworkUsageDescription"] == "Poolhouse finds and joins your other devices on this network."


def test_the_requirement_names_the_id_and_the_certificate_never_a_cdhash():
    text = runtime_identity.requirement(SHA1)
    assert text == f'designated => identifier "app.poolhouse.runtime" and certificate leaf = H"{SHA1.lower()}"'
    assert runtime_identity.stable_requirement(text)
    assert not runtime_identity.stable_requirement('designated => cdhash H"ab"')
    assert not runtime_identity.stable_requirement('designated => identifier "app.poolhouse.runtime"'), "an ad hoc requirement is not stable"
    assert not runtime_identity.stable_requirement(text.replace("app.poolhouse.runtime", "python.exe"))


def test_a_unit_starts_its_launcher_under_the_poolhouse_executable_where_there_is_one():
    argv = ["/h/bin/poolhouse-traind", "--port=8770"]
    assert runtime_identity.launch_argv(argv) == argv, "a machine with no such executable runs its units as it always did"
    runtime_identity.executable_path().parent.mkdir(parents=True)
    runtime_identity.executable_path().write_bytes(b"x")
    assert runtime_identity.launch_argv(argv) == [str(runtime_identity.executable_path()), "-I", "-S", *argv]


def test_a_runtime_identity_that_is_not_a_plain_name_cannot_leave_the_runtime_directory():
    with pytest.raises(runtime_identity.IdentityError, match="not a runtime identity"):
        runtime_identity.bundle_path("../../elsewhere")


def test_a_truncated_or_hostile_executable_names_no_libraries_and_raises_nothing(tmp_path):
    for name, data in {"short-fat": struct.pack(">II", 0xCAFEBABE, 9), "cut-fat": struct.pack(">II", 0xCAFEBABE, 1) + b"\0" * 10,
                       "cut-thin": thin("/usr/lib/libSystem.B.dylib")[:40], "noise": bytes(range(256)) * 4}.items():
        (tmp_path / name).write_bytes(data)
        assert macho.dependencies(tmp_path / name) == [], name


def test_a_listener_beyond_this_machine_under_an_interpreter_is_found_and_nothing_else_is():
    apps = node_app.apps_directory()
    ours = str(runtime_identity.executable_path())
    node = str(node_app.executable_path())
    rows = [("/py/bin/python3.13", "10.0.0.5"), ("/py/bin/python3.13", "127.0.0.1"), ("/py/bin/python3.13", "::1"),
            (ours, "10.0.0.5"), (node, "192.168.1.5"), ("/usr/bin/other", "::")]
    assert runtime_identity.exposed_listeners(rows) == [("/py/bin/python3.13", "10.0.0.5"), ("/usr/bin/other", "::")]
    assert apps in Path(ours).parents and apps in Path(node).parents


def test_load_commands_name_the_libraries_an_executable_loads(tmp_path):
    thin_file = tmp_path / "thin"
    thin_file.write_bytes(thin("/usr/lib/libSystem.B.dylib", "@executable_path/../lib/libpython3.13.dylib"))
    assert macho.dependencies(thin_file) == ["/usr/lib/libSystem.B.dylib", "@executable_path/../lib/libpython3.13.dylib"]
    universal = tmp_path / "universal"
    universal.write_bytes(fat((0x01000007, thin("/x86", cpu=0x01000007)), (0x0100000C, thin("/arm", cpu=0x0100000C))))
    for machine, name in (("arm64", "/arm"), ("x86_64", "/x86")):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(macho.platform, "machine", lambda machine=machine: machine)
            assert macho.dependencies(universal) == [name]
    script = tmp_path / "script"
    script.write_text("#!/bin/sh\n")
    assert macho.dependencies(script) == []


def test_libraries_loaded_from_beside_the_interpreter_come_with_it(tmp_path):
    interpreter, lib = tmp_path / "bin" / "python3.13", tmp_path / "lib" / "libpython3.13.dylib"
    interpreter.parent.mkdir()
    lib.parent.mkdir()
    interpreter.write_bytes(thin("/usr/lib/libSystem.B.dylib", "@executable_path/../lib/libpython3.13.dylib"))
    lib.write_bytes(thin("/usr/lib/libSystem.B.dylib"))
    assert runtime_identity.parts(interpreter) == [(interpreter, "Poolhouse"), (lib, "../lib/libpython3.13.dylib")]
    assert runtime_identity.digest(runtime_identity.parts(interpreter)) != runtime_identity.digest([(interpreter, "Poolhouse")])


def test_a_library_that_would_sit_outside_the_bundle_is_refused(tmp_path):
    interpreter = tmp_path / "python"
    interpreter.write_bytes(thin("@executable_path/../../elsewhere.dylib"))
    (tmp_path.parent / "elsewhere.dylib").write_bytes(thin())
    with pytest.raises(runtime_identity.IdentityError, match="cannot sit inside"):
        runtime_identity.parts(interpreter)


def test_without_the_certificate_nothing_is_signed_and_nothing_is_left_behind(tmp_path):
    interpreter = tmp_path / "python"
    interpreter.write_bytes(thin("/usr/lib/libSystem.B.dylib"))

    def refuse(bundle, identifier, requirement_for):
        raise node_signing.SigningError("the keychain is locked")

    with pytest.raises(runtime_identity.IdentityError, match=r"could not sign.*keychain is locked.*keeps the identity it had"):
        runtime_identity.ensure_bundle(interpreter, signer=refuse)
    assert not runtime_identity.bundle_path().exists() and not (runtime_identity.bundle_path().parent / ".building").exists()


def test_the_node_bundle_still_falls_back_to_ad_hoc_but_the_runtime_never_does(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(node_signing, "_run", lambda argv: ran.append(argv) or "")
    monkeypatch.setattr(node_signing, "ensure_certificate", lambda: "")
    with pytest.raises(node_signing.SigningError):
        node_signing.sign_with_certificate(tmp_path, "id", lambda _s: "req")
    assert ran == [], "no signature of any kind was made"
    assert node_signing.sign(tmp_path, "id", lambda _s: "req", "adhoc") == "ad-hoc" and ran[0][3] == "-"


@pytest.mark.usefixtures("macos")
def test_a_runtime_started_through_the_bundle_is_the_poolhouse_executable_to_the_kernel(tmp_path, ad_hoc):
    chosen = make_runtime(tmp_path)
    before = runtime_identity.interpreter_of(chosen.prefix)
    executable = runtime_identity.bind(chosen, signer=ad_hoc)
    assert runtime_identity.bound(chosen.prefix) and runtime_identity.interpreter_of(chosen.prefix) == Path(os.path.realpath(executable))
    assert runtime_identity.unbound(chosen.prefix / "bin" / "python") == before, "a runtime built from this one points at Python, not at the copy"
    child = subprocess.Popen([str(chosen.python), "-I", "-c", "import time; time.sleep(20)"])
    try:
        time.sleep(0.5)
        process = psutil.Process(child.pid)
        assert Path(process.exe()) == Path(os.path.realpath(executable)) and process.name() == "Poolhouse"
    finally:
        child.kill()
        child.wait()
    signed = subprocess.run(["codesign", "-dvv", str(executable)], capture_output=True, text=True, check=False).stderr
    assert "Identifier=app.poolhouse.runtime" in signed
    sealed = json.loads(subprocess.run(["plutil", "-convert", "json", "-o", "-", str(runtime_identity.bundle_path() / "Contents" / "Info.plist")],
                                       capture_output=True, text=True, check=True).stdout)
    assert sealed["CFBundleIdentifier"] == "app.poolhouse.runtime" and sealed["CFBundleName"] == "Poolhouse"


@pytest.mark.usefixtures("macos")
def test_nothing_listens_beyond_loopback_under_an_interpreter_path(tmp_path, ad_hoc):
    chosen = make_runtime(tmp_path)
    runtime_identity.bind(chosen, signer=ad_hoc)
    code = "import socket,sys,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); print(s.getsockname()[1], flush=True); time.sleep(30)"
    child = subprocess.Popen([str(chosen.python), "-I", "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        port = int(child.stdout.readline())
        rows = [(psutil.Process(child.pid).exe(), c.laddr.ip) for c in psutil.Process(child.pid).net_connections(kind="inet") if c.laddr.port == port]
        assert rows and runtime_identity.exposed_listeners(rows) == []
        assert runtime_identity.exposed_listeners([(str(runtime_identity.interpreter_of(Path(sys.prefix))), "10.0.0.5")]), \
            "the same listener on the network, under the interpreter Python was installed with, is the thing this finds"
    finally:
        child.kill()
        child.wait()


@pytest.mark.usefixtures("macos")
def test_the_requirement_does_not_change_when_the_executable_is_rebuilt(tmp_path, ad_hoc):
    chosen = make_runtime(tmp_path)
    runtime_identity.bind(chosen, signer=ad_hoc)
    bundle = runtime_identity.bundle_path()
    first = runtime_identity.requirement_of(bundle)
    first_hash = subprocess.run(["codesign", "-dvvv", str(bundle)], capture_output=True, text=True, check=False).stderr.split("CDHash=")[1].split()[0]
    changed = tmp_path / "python-again"
    shutil.copyfile(runtime_identity.interpreter_of(chosen.prefix), changed)
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "python.again", str(changed)], check=True, capture_output=True)  # another file, the same program
    runtime_identity.ensure_bundle(changed, signer=ad_hoc)
    second_hash = subprocess.run(["codesign", "-dvvv", str(bundle)], capture_output=True, text=True, check=False).stderr.split("CDHash=")[1].split()[0]
    assert first_hash != second_hash, "the bundle was rebuilt"
    assert runtime_identity.requirement_of(bundle) == first, "and what macOS keeps its answer against did not move"
    assert runtime_identity.source_of(bundle)["interpreter"] == str(changed)


@pytest.mark.usefixtures("macos")
def test_a_runtime_that_does_not_start_as_poolhouse_keeps_the_interpreter_it_had(tmp_path, ad_hoc, monkeypatch):
    chosen = make_runtime(tmp_path)
    before = runtime_identity.interpreter_of(chosen.prefix)

    def refuse(prefix, executable):
        raise runtime_identity.IdentityError("the runtime started as /usr/bin/python3")

    monkeypatch.setattr(runtime_identity, "_probe", refuse)
    with pytest.raises(runtime_identity.IdentityError, match="started as /usr/bin/python3"):
        runtime_identity.bind(chosen, signer=ad_hoc)
    assert runtime_identity.interpreter_of(chosen.prefix) == before and not runtime_identity.bound(chosen.prefix)
    assert subprocess.run([str(chosen.python), "-I", "-c", "pass"], check=False).returncode == 0


@pytest.mark.usefixtures("macos")
def test_binding_twice_changes_nothing_and_a_removed_bundle_is_made_again(tmp_path, ad_hoc):
    chosen = make_runtime(tmp_path)
    first = runtime_identity.bind(chosen, signer=ad_hoc)
    stamp = first.stat().st_ino
    assert runtime_identity.bind(chosen, signer=ad_hoc) == first and first.stat().st_ino == stamp
    shutil.rmtree(runtime_identity.bundle_path())
    assert runtime_identity.bind(chosen, signer=ad_hoc) == first and first.is_file(), "the runtime itself says which Python it was made from"
    assert str(runtime_identity.base_of(chosen.prefix)) == runtime_identity.source_of(runtime_identity.bundle_path())["interpreter"]


@pytest.mark.usefixtures("macos")
@pytest.mark.slow
def test_the_real_daemon_reports_the_poolhouse_executable_and_its_bundle_id(tmp_path, ad_hoc, monkeypatch):
    from test_setup import _booted_daemon

    chosen = make_runtime(tmp_path)
    executable = runtime_identity.bind(chosen, signer=ad_hoc)
    (next((chosen.prefix / "lib").glob("python*")) / "site-packages" / "tests.pth").write_text(
        "\n".join(entry for entry in sys.path if entry and Path(entry).is_dir()), encoding="utf-8")  # what this suite imports, for the daemon
    monkeypatch.setattr(sys, "executable", str(chosen.python))
    with _booted_daemon(tmp_path) as (_key, port, _disco, _log):
        from poolhouse.fleet.launch import already_running

        said = already_running(port)
    assert said["process"]["bundle_id"] == "app.poolhouse.runtime" and said["process"]["name"] == "Poolhouse"
    assert said["process"]["executable"] == str(Path(os.path.realpath(executable)).relative_to(Path(os.path.realpath(home()))))
    assert str(chosen.prefix) not in json.dumps(said) and str(Path.home()) not in said["process"]["executable"], "no directory of the account is named"


@pytest.mark.slow
def test_the_daemon_of_a_machine_that_is_not_bound_says_which_program_it_is(tmp_path):
    from test_setup import _booted_daemon

    from poolhouse.fleet.launch import already_running

    with _booted_daemon(tmp_path) as (_key, port, _disco, _log):
        said = already_running(port)
    assert said["process"]["executable"] == Path(os.path.realpath(sys.executable)).name
    assert said["process"]["bundle_id"] == "" and said["process"]["name"] == Path(said["process"]["executable"]).name


def test_a_machine_that_is_not_the_installed_software_binds_nothing(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(runtime_identity, "bind", lambda chosen, **k: called.append(chosen))
    chosen = runtime.Runtime(tmp_path / "r", COMMIT, "0", runtime.identity())
    assert runtime_identity.adopt(chosen) == "" and called == [], "a test, a checkout and another state root never reach the keychain"
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    monkeypatch.setattr(runtime_identity, "bound", lambda prefix, identity=None: False)
    assert runtime_identity.adopt(chosen).endswith("now runs as Poolhouse") and called == [chosen]
    monkeypatch.setattr(runtime_identity, "bound", lambda prefix, identity=None: True)
    assert runtime_identity.adopt(chosen) == "" and len(called) == 2


def test_a_unit_is_refused_while_the_installed_mac_has_no_signed_executable(monkeypatch):
    assert runtime_identity.unit_problem() == "", "nowhere but the installed software on a Mac is a signed executable required"
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    assert "no signed executable yet" in runtime_identity.unit_problem()


def test_a_process_outside_the_bundle_reports_no_bundle_id():
    report = runtime_identity.process_report()
    assert report["bundle_id"] == "" and report["executable"] == Path(runtime_identity.kernel_executable()).name
    assert not runtime_identity.inside(sys.executable)


def built_runtime(tmp_path: Path) -> runtime.Runtime:
    return runtime.Runtime(tmp_path / "prefix", COMMIT, "0", runtime.identity())


def test_the_build_makes_the_runtime_run_as_poolhouse_before_the_node_is_added(tmp_path, monkeypatch):
    chosen, order = built_runtime(tmp_path), []
    monkeypatch.setattr(runtime_deploy, "host_python", lambda: tmp_path / "python")
    monkeypatch.setattr(runtime_deploy.runtime_wheel, "build", lambda *a, **k: chosen)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "adopt", lambda built: order.append("adopt") or "")
    monkeypatch.setattr(runtime_deploy.node_build, "add_to", lambda built, source, commit, **k: order.append("node"))
    assert runtime_deploy.build(runtime_deploy.Plan(tmp_path, COMMIT, None), tmp_path, chosen.prefix) == chosen
    assert order == ["adopt", "node"]


def test_a_runtime_that_cannot_run_as_poolhouse_is_discarded_and_never_selected(tmp_path, monkeypatch):
    chosen, discarded = built_runtime(tmp_path), []

    def refuse(built):
        raise runtime_identity.IdentityError("the keychain is locked")

    monkeypatch.setattr(runtime_deploy, "host_python", lambda: tmp_path / "python")
    monkeypatch.setattr(runtime_deploy.runtime_wheel, "build", lambda *a, **k: chosen)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "adopt", refuse)
    monkeypatch.setattr(runtime_deploy.runtime_store, "discard", discarded.append)
    with pytest.raises(runtime_identity.IdentityError, match="keychain is locked"):
        runtime_deploy.build(runtime_deploy.Plan(tmp_path, COMMIT, None), tmp_path, chosen.prefix)
    assert discarded == [chosen.prefix]


def test_the_host_python_is_never_the_copy_in_the_bundle(tmp_path, monkeypatch):
    bundle_copy = runtime_identity.executable_path()
    bundle_copy.parent.mkdir(parents=True)
    bundle_copy.write_bytes(b"x")
    real = tmp_path / "python3.13"
    real.write_bytes(b"x")
    resources = runtime_identity.bundle_path() / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "source.json").write_text(json.dumps({"interpreter": str(real)}))
    monkeypatch.setattr(runtime_deploy.Environment, "host_python", lambda self: bundle_copy)
    assert runtime_deploy.host_python() == real


def test_ensure_with_nothing_to_build_still_makes_the_selected_runtime_run_as_poolhouse(tmp_path, monkeypatch):
    chosen = built_runtime(tmp_path)
    monkeypatch.setattr(runtime_deploy, "settled", lambda plan: "current")
    monkeypatch.setattr(runtime_deploy.runtime_store, "selection", lambda root=None: {"prefix": str(chosen.prefix)})
    monkeypatch.setattr(runtime_deploy.runtime_identity, "applies", lambda: True)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "bound", lambda prefix, identity=None: False)
    monkeypatch.setattr(runtime_deploy.runtime, "available", lambda: chosen)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "adopt", lambda picked: "aaaaaaa now runs as Poolhouse")
    outcome = runtime_deploy.ensure(runtime_deploy.Plan(tmp_path, COMMIT, None))
    assert (outcome.action, outcome.detail) == ("current", "aaaaaaa now runs as Poolhouse")

    def refuse(picked):
        raise runtime_identity.IdentityError("Poolhouse could not sign its executable (locked)")

    monkeypatch.setattr(runtime_deploy.runtime_identity, "adopt", refuse)
    said = runtime_deploy.ensure(runtime_deploy.Plan(tmp_path, COMMIT, None))
    assert said.action == "current" and said.detail.startswith("not running as Poolhouse: Poolhouse could not sign"), "a working runtime is not taken down"


def test_a_runtime_that_already_runs_as_poolhouse_costs_ensure_no_start_of_python(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_deploy.runtime_store, "selection", lambda root=None: {"prefix": str(tmp_path)})
    monkeypatch.setattr(runtime_deploy.runtime_identity, "applies", lambda: True)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "bound", lambda prefix, identity=None: True)
    monkeypatch.setattr(runtime_deploy.runtime, "available", lambda: pytest.fail("the interpreter was started to look"))
    assert runtime_deploy._selection_identity(NOWHERE) == []


def test_a_host_that_does_not_run_as_poolhouse_is_replaced_even_on_the_selected_commit(monkeypatch):
    target = runtime_host._Target(COMMIT, "d" * 64)
    said = {"launcher_control": "a" * 32, "commit": COMMIT, "runtime_prefix_digest": "d" * 64}
    assert runtime_host._ready(said, target), "a machine that is not the installed software asks nothing of the host"
    monkeypatch.setattr(runtime_host.runtime_identity, "applies", lambda: True)
    assert not runtime_host._ready(said, target), "a host that predates the identity does not say what it runs as"
    assert not runtime_host._ready({**said, "process": {"bundle_id": ""}}, target)
    assert runtime_host._ready({**said, "process": {"bundle_id": "app.poolhouse.runtime"}}, target)


def test_the_daemon_finding_names_the_program_macos_asks_about():
    ours = setup.identity_finding({"process": {"executable": "/h/Poolhouse", "bundle_id": "app.poolhouse.runtime"}})
    assert ours.good and "Poolhouse" in ours.said and ours.fix == []
    python = setup.identity_finding({"process": {"executable": "/py/bin/python3.13", "bundle_id": ""}})
    assert not python.good and python.said == "/py/bin/python3.13" and python.fix == ["poolhouse", "runtime", "ensure"]
    assert not setup.identity_finding({}).good


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a Linux process takes its name from prctl")
def test_a_linux_daemon_is_called_poolhouse_by_ps_and_ss():
    was = Path("/proc/self/comm").read_text().strip()
    try:
        assert poolhouse_platform.name_process("poolhouse") is True
        assert Path("/proc/self/comm").read_text().strip() == "poolhouse"
    finally:
        poolhouse_platform.name_process(was)


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="only Linux takes a name set from inside")
def test_no_other_system_takes_a_process_name_from_inside():
    assert poolhouse_platform.name_process("poolhouse") is False


@pytest.mark.usefixtures("macos")
def test_the_executable_starts_by_itself_for_a_unit_that_gives_it_no_virtual_environment(tmp_path, ad_hoc):
    chosen = make_runtime(tmp_path)
    executable = runtime_identity.bind(chosen, signer=ad_hoc)
    assert runtime_identity.bare_problem(executable) == ""
    pyvenv = (runtime_identity.bundle_path() / "Contents" / "MacOS" / "pyvenv.cfg").read_text()
    assert f"home = {runtime_identity.base_of(chosen.prefix).parent}" in pyvenv
    done = subprocess.run([str(executable), "-I", "-S", "-c", "import json, subprocess; print('up')"], capture_output=True, text=True, check=False)
    assert done.stdout.strip() == "up", done.stderr
    executable.write_bytes(b"not a program")
    assert "does not start on its own" in runtime_identity.bare_problem(executable)


@pytest.mark.usefixtures("macos")
def test_a_bundle_is_replaced_in_one_step_with_no_moment_without_one(tmp_path, ad_hoc, monkeypatch):
    chosen = make_runtime(tmp_path)
    runtime_identity.bind(chosen, signer=ad_hoc)
    bundle = runtime_identity.bundle_path()
    changed = tmp_path / "python-again"
    shutil.copyfile(runtime_identity.base_of(chosen.prefix), changed)
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "python.again", str(changed)], check=True, capture_output=True)
    taken = []
    monkeypatch.setattr(runtime_identity, "promote", lambda *a, **k: taken.append(a) or pytest.fail("the old bundle was moved aside first"))
    runtime_identity.ensure_bundle(changed, signer=ad_hoc)
    assert taken == [] and runtime_identity.source_of(bundle)["interpreter"] == str(changed)
    assert not (bundle.parent / ".replaced").exists() and not (bundle.parent / ".building").exists()


def test_a_bundle_signed_with_another_certificate_than_this_macs_is_not_the_poolhouse_executable(tmp_path, monkeypatch):
    bundle = runtime_identity.bundle_path()
    runtime_identity.executable_path().parent.mkdir(parents=True)
    runtime_identity.executable_path().write_bytes(b"x")
    monkeypatch.setattr(runtime_identity, "signature_problem", lambda path: "")
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    monkeypatch.setattr(runtime_identity, "requirement_of", lambda path: runtime_identity.requirement("a" * 40))
    monkeypatch.setattr(node_signing, "certificate_sha1", lambda: "a" * 40)
    assert runtime_identity.bundle_problem() == "", bundle
    monkeypatch.setattr(node_signing, "certificate_sha1", lambda: "b" * 40)
    assert "another certificate" in runtime_identity.bundle_problem(), "a stranger's self-signed certificate has the same shape of requirement"
    monkeypatch.setattr(node_signing, "certificate_sha1", lambda: "")
    assert "is not in the login keychain" in runtime_identity.bundle_problem()


def test_a_tool_that_hangs_or_is_missing_is_a_failed_check_not_an_exception(monkeypatch):
    def hang(argv, **k):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(runtime_identity.subprocess, "run", hang)
    assert runtime_identity.requirement_of(Path("/x")) == "" and "does not verify" in runtime_identity.signature_problem(Path("/x"))


def test_a_failed_attempt_to_run_as_poolhouse_is_recorded_and_not_repeated_at_once(tmp_path, monkeypatch):
    chosen, attempts = built_runtime(tmp_path), []

    def refuse(picked):
        attempts.append(picked)
        raise runtime_identity.IdentityError("the keychain is locked")

    monkeypatch.setattr(runtime_deploy.runtime_store, "selection", lambda root=None: {"prefix": str(chosen.prefix)})
    monkeypatch.setattr(runtime_deploy.runtime_identity, "applies", lambda: True)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "bound", lambda prefix, identity=None: False)
    monkeypatch.setattr(runtime_deploy.runtime, "available", lambda: chosen)
    monkeypatch.setattr(runtime_deploy.runtime_identity, "adopt", refuse)
    first = runtime_deploy._selection_identity(NOWHERE)
    assert len(attempts) == 1 and "keychain is locked" in first[0]
    assert runtime_deploy.runtime_store.read_state()["identity_failure"]["commit"] == COMMIT
    again = runtime_deploy._selection_identity(NOWHERE)
    assert len(attempts) == 1 and "keychain is locked" in again[0] and "tried again after" in again[0], "no prompt every time ensure runs"
    monkeypatch.setattr(runtime_deploy.runtime_identity, "adopt", lambda picked: "")
    runtime_deploy.runtime_store.write_state({"identity_failure": None})
    assert runtime_deploy._selection_identity(NOWHERE) == []


def unbound_world(tmp_path, monkeypatch):
    """A Mac, a selected runtime that still runs as its bare Python, and a process that is not the installed software."""
    chosen = built_runtime(tmp_path)
    (chosen.prefix / "bin").mkdir(parents=True)
    (chosen.prefix / "bin" / "python").symlink_to(sys.executable)
    monkeypatch.setattr(runtime_identity, "_mac", lambda: True)
    monkeypatch.setattr(runtime_deploy.runtime_store, "selection", lambda root=None: {"prefix": str(chosen.prefix)})
    monkeypatch.setattr(runtime_deploy.runtime, "available", lambda: chosen)
    return chosen


@pytest.mark.parametrize("path", ["settled", "healthy"])
def test_ensure_run_from_software_that_is_not_installed_says_it_did_not_bind_and_the_one_command_that_does(tmp_path, monkeypatch, path):
    chosen = unbound_world(tmp_path, monkeypatch)
    plan = runtime_deploy.Plan(tmp_path, COMMIT, tmp_path / "bin")
    monkeypatch.setattr(runtime_deploy, "settled", lambda _plan: "current" if path == "settled" else "")
    if path == "healthy":
        monkeypatch.setattr(runtime_deploy, "healthy", lambda _plan: chosen)
        monkeypatch.setattr(runtime_deploy.runtime_store, "collect", lambda keep: None)
    outcome = runtime_deploy.ensure(plan)
    assert outcome.action == "current"
    assert "NOT bound to the Poolhouse executable" in outcome.detail
    assert real_machine.refusal() in outcome.detail and Path(sys.executable).name in outcome.detail, "it says why, and what the host stays"
    assert f"run `{tmp_path / 'bin' / 'poolhouse'} runtime ensure`" in outcome.detail


def test_a_runtime_switched_to_from_software_that_is_not_installed_says_it_is_not_bound(tmp_path, monkeypatch):
    chosen = unbound_world(tmp_path, monkeypatch)
    plan = runtime_deploy.Plan(tmp_path, "b" * 40, tmp_path / "bin")
    monkeypatch.setattr(runtime_deploy, "healthy", lambda _plan: None)
    monkeypatch.setattr(runtime_deploy, "_recovered", lambda _plan, _who: (None, []))
    monkeypatch.setattr(runtime_deploy, "acting", lambda _plan: None)
    monkeypatch.setattr(runtime_deploy, "build_and_switch", lambda *a, **k: chosen)
    for name in ("_note", "_reject_below_floor"):
        monkeypatch.setattr(runtime_deploy, name, lambda *a, **k: None)
    monkeypatch.setattr(runtime_deploy.runtime_store, "collect", lambda keep: None)
    outcome = runtime_deploy._ensured(plan, False, False, runtime_deploy.build)
    assert outcome.action == "switched" and "NOT bound to the Poolhouse executable" in outcome.detail
    assert f"{tmp_path / 'bin' / 'poolhouse'} runtime ensure" in outcome.detail


def test_nothing_is_said_where_there_is_nothing_to_bind(tmp_path, monkeypatch):
    chosen = unbound_world(tmp_path, monkeypatch)
    command = "poolhouse runtime ensure"
    assert runtime_identity.unbound_note(chosen, command)
    monkeypatch.setattr(runtime_identity, "_mac", lambda: False)
    assert runtime_identity.unbound_note(chosen, command) == "", "another system has no executable to bind"
    monkeypatch.setattr(runtime_identity, "_mac", lambda: True)
    monkeypatch.setattr(runtime_identity, "bound", lambda prefix, identity=None: True)
    assert runtime_identity.unbound_note(chosen, command) == "", "a runtime that already runs as Poolhouse has nothing left undone"
    monkeypatch.setattr(runtime_identity, "bound", lambda prefix, identity=None: False)
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    assert runtime_identity.unbound_note(chosen, command) == "", "the installed software binds it or fails with the reason"
