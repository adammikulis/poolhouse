"""The open box of a macOS coding session, used and attacked from inside the real confinement: what a shell there may do it does, and each named
harm is refused.

Owner-order: "can't you just make the sandbox just like the rest of things so the agent can test whatever they need safely?", "we really need
tests to be able to be run in the sandbox", "including with gpu", "so if your sandbox stops things like gpu tests, browser tests, etc, it is not
useful", "No agent should be inhibited from doing their work that is given them."

The launch is the real one (`tests.test_redteam_isolation_shell.shelled`: the stage, both bridges, the proof, the Seatbelt profile). The program
in the harness's place is a shell script (NOT A MODEL and NOT A HARNESS) and the model server is the kit's marked plumbing server.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import loopback_window
from poolhouse.isolation import confinement as choke_point, toolchain
from poolhouse.sandbox import confinement, process_tree, seatbelt_open
from tests.test_redteam_isolation_shell import box, shelled  # noqa: F401  (box is a fixture)

pytest_plugins = ["tests.confined_kit", "tests.sandbox_kit"]
pytestmark = pytest.mark.usefixtures("confinable", "seatbelt")

PYTHON = shlex.quote(os.path.realpath(sys.executable))


def run(code: str) -> str:
    """One line of the shell script that runs ``code`` with the interpreter, as one `tried` attempt takes it."""
    return shlex.quote(f"{PYTHON} -I -c {shlex.quote(code)}")


def outcomes(out: str) -> dict[str, str]:
    """Each `tried` attempt by name and whether it was ``OPEN`` or ``shut``."""
    return {line.split(" ", 1)[1]: line.split(" ", 1)[0] for line in out.splitlines() if line.startswith(("OPEN ", "shut "))}


def expect(out: str, opened: tuple[str, ...], shut: tuple[str, ...]) -> None:
    wanted = {**dict.fromkeys(opened, "OPEN"), **dict.fromkeys(shut, "shut")}
    assert outcomes(out) == wanted, ({k: (v, outcomes(out).get(k)) for k, v in wanted.items() if outcomes(out).get(k) != v}, out)


@pytest.fixture
def earlier():
    """A folder another program made in the shared temporary folder before the launch, holding a file and a socket it listens on."""
    root = Path(tempfile.mkdtemp(prefix="other-", dir="/tmp")).resolve()
    (root / "theirs").write_text("NOT-A-REAL-SECRET another program's\n")
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(root / "s"))
    listener.listen(4)
    try:
        yield root
    finally:
        listener.close()
        for each in root.iterdir():
            each.unlink()
        root.rmdir()


def test_an_open_box_writes_its_worktree_temporary_files_and_caches_and_nothing_else(box, earlier):  # noqa: F811
    house = box.root / "person"
    house.mkdir()
    (house / "notes").write_text("NOT-A-REAL-SECRET the person's\n")
    mine = pwd.getpwuid(os.getuid()).pw_dir
    made = f"/tmp/open-box-{os.getpid()}"
    shared = toolchain.user_temporary()
    out, _, _ = shelled(box, f"""\
        tried worktree 'echo x > made-here'
        tried agent-home 'echo x > "$HOME/mine"'
        tried tmpdir 'echo x > "$TMPDIR/mine"'
        tried here-document-of-zsh '/bin/zsh -c "cat <<END >/dev/null\nx\nEND"'
        tried shared-temporary 'echo x > {made}'
        tried temporary-of-libraries 'f=$(/usr/bin/getconf DARWIN_USER_TEMP_DIR) && echo x > "$f/of-libraries"'
        tried socket-of-its-own {run("import socket, tempfile; p = tempfile.mkdtemp() + '/s'; s = socket.socket(socket.AF_UNIX); s.bind(p); s.listen(); socket.socket(socket.AF_UNIX).connect(p)")}
        tried write-person 'echo x > {house}/planted'
        tried change-person 'echo x >> {house}/notes'
        tried read-person 'cat {house}/notes'
        tried list-home 'ls {shlex.quote(mine)}'
        tried list-home-by-the-data-volume 'ls /System/Volumes/Data{shlex.quote(mine)}'
        tried list-volumes 'ls /Volumes/*/'
        tried write-system 'echo x > /usr/local/poolhouse-planted'
        tried read-other-temporary 'cat {earlier}/theirs'
        tried write-other-temporary 'echo x > {earlier}/planted'
        tried move-other-temporary 'mv {earlier} {earlier}.moved'
        tried link-other-temporary 'ln {earlier}/theirs "$TMPDIR/linked"'
        tried link-person 'ln {house}/notes ./linked'
        tried write-account-temporary 'echo x > {shared}/poolhouse-planted'
        tried read-keychains 'ls /Library/Keychains'
        tried read-host-keys 'cat /private/etc/ssh/ssh_config'
        """)
    expect(out, ("worktree", "agent-home", "tmpdir", "here-document-of-zsh", "temporary-of-libraries", "socket-of-its-own"),
           ("shared-temporary", "write-person", "change-person", "read-person", "list-home", "list-home-by-the-data-volume", "list-volumes",
            "write-system", "read-other-temporary", "write-other-temporary", "move-other-temporary", "link-other-temporary", "link-person",
            "write-account-temporary", "read-keychains", "read-host-keys"))
    assert "NOT-A-REAL-SECRET" not in out and not (house / "planted").exists() and (earlier / "theirs").is_file(), out
    assert not os.path.lexists(made)


def test_what_other_programs_make_in_the_shared_temporary_folder_after_the_launch_is_not_listed_read_written_or_reached(box):  # noqa: F811
    later = Path(f"/private/tmp/later-{os.getpid()}")
    listener = socket.socket(socket.AF_UNIX)

    def made_meanwhile(_confinement) -> None:
        """Another program's folder, file and socket, made once the box's profile stands and before its program runs."""
        if not later.exists():
            later.mkdir(mode=0o700)
            (later / "theirs").write_text("NOT-A-REAL-SECRET another program's\n")
            listener.bind(str(later / "s"))
            listener.listen(4)

    try:
        out, _, _ = shelled(box, f"""\
            tried list-shared 'ls /tmp/'
            tried list-shared-by-its-real-path 'ls /private/tmp'
            tried list-other-shared 'ls /private/var/tmp'
            tried read-later 'cat {later}/theirs'
            tried write-later 'echo x > {later}/planted'
            tried replace-later 'rm {later}/theirs'
            tried make-beside 'mkdir {later}-planted'
            tried later-socket {run(f"import socket; socket.socket(socket.AF_UNIX).connect({str(later / 's')!r})")}
            """, look=made_meanwhile)
        expect(out, (), ("list-shared", "list-shared-by-its-real-path", "list-other-shared", "read-later", "write-later", "replace-later",
                         "make-beside", "later-socket"))
        assert "NOT-A-REAL-SECRET" not in out and (later / "theirs").is_file() and not os.path.lexists(f"{later}-planted"), out
    finally:
        listener.close()
        for each in (later / "theirs", later / "s", later / "planted"):
            each.unlink(missing_ok=True)
        if later.is_dir():
            later.rmdir()


OUTSIDER = """\
import json, os, socket, sys, time
window, session, report, wait = sys.argv[1:5]
while wait and os.getppid() != 1:
    time.sleep(0.01)
got = {}
with socket.socket(socket.AF_UNIX) as asking:
    asking.settimeout(10)
    asking.connect(window)
    asking.sendall(json.dumps({"family": 4, "port": 0, "reuse": False}).encode())
    try:
        said, handed, _, _ = socket.recv_fds(asking, 128, 1)
    except OSError:
        said, handed = b"", []
    got["port"] = bool(handed)
    for each in handed:
        os.close(each)
with socket.socket(socket.AF_UNIX) as asking:
    asking.settimeout(10)
    asking.connect(session)
    try:
        asking.sendall(b'{"verb": "tools"}\\n')
        got["frame"] = bool(asking.recv(65536))
    except OSError:
        got["frame"] = False
with open(report + ".part", "w") as out:
    json.dump(got, out)
os.rename(report + ".part", report)
"""
"""A process of this user that is none of the launch's: asks the window of a session for a port and its session bridge for an answer."""


def reported(report: Path) -> dict:
    deadline = time.monotonic() + 30
    while not report.exists():
        assert time.monotonic() < deadline, "the other process reported nothing"
        time.sleep(0.02)
    return json.loads(report.read_text())


def test_a_sessions_port_window_and_bridge_answer_no_process_but_the_launchs_own(box):  # noqa: F811
    """Before the harness runs, a process that left the launcher's tree; while it runs, any process outside the harness's tree, the launcher's
    own among them. The box's own processes are given ports all along."""
    outsider = box.root / "outsider.py"
    outsider.write_text(OUTSIDER)
    before, during = box.root / "before.json", box.root / "during.json"
    seen: list[threading.Thread] = []

    def attacked(confinement) -> None:
        if seen:
            return
        window = confinement.env[loopback_window.ENV].partition("@")[2]
        session = next(each for each in confinement.profile.policy.unix_sockets if each != window)
        words = [sys.executable, "-I", str(outsider), window, session]
        # a shell that starts it and ends at once, so the system's first process takes it over
        subprocess.run(["/bin/sh", "-c", f"{shlex.join([*words, str(before), 'orphaned'])} >/dev/null 2>&1 &"], check=True)
        reported(before)
        stage = Path(confinement.profile.stage)

        def meanwhile() -> None:
            deadline = time.monotonic() + 30
            while not (stage / "running").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            subprocess.run([*words, str(during), ""], check=False, timeout=30)
            (stage / "attacked").write_text("done\n")

        seen.append(threading.Thread(target=meanwhile, daemon=True))
        seen[0].start()

    (box.project / "serve.py").write_text("import socket\nsocket.create_server(('127.0.0.1', 0))\n")
    out, _, _ = shelled(box, f"""\
        tried own-port-from-a-grandchild '/bin/sh -c "{PYTHON} serve.py; exit $?"'
        : > running
        n=0; while [ ! -f attacked ] && [ $n -lt 600 ]; do sleep 0.05; n=$((n+1)); done
        tried own-port-afterwards '{PYTHON} serve.py'
        """, look=attacked)
    expect(out, ("own-port-from-a-grandchild", "own-port-afterwards"), ())
    assert reported(before) == {"port": False, "frame": False}, "before the harness ran"
    assert reported(during) == {"port": False, "frame": False}, "while the harness ran"


def test_a_process_that_took_the_pid_of_an_ended_member_is_not_admitted():
    member = subprocess.Popen([os.path.realpath(sys.executable), "-c", "import time; time.sleep(120)"])
    tree = process_tree.follow(member.pid)
    admission = choke_point.Admission()
    admission.tree = tree
    try:
        assert admission(member.pid) and not admission(os.getpid())
        tree.begin(member.pid, tree.began(member.pid) - 100.0)   # what the tree noted is an earlier process that had this pid
        assert not admission(member.pid)
    finally:
        process_tree.release(tree)
        member.kill()
        member.wait()


def test_an_open_box_runs_the_installed_tools_and_reads_none_of_their_credentials_or_the_persons_files(box):  # noqa: F811
    mine = Path(os.environ["HOME"])   # the suite's own home, which the launcher takes for the person's
    (mine / ".cargo" / "bin").mkdir(parents=True)
    (mine / ".cargo" / "bin" / "installed-tool").write_text("#!/bin/sh\necho installed-tool ran\n")
    (mine / ".cargo" / "bin" / "installed-tool").chmod(0o755)
    (mine / ".cargo" / "credentials.toml").write_text('token = "NOT-A-REAL-TOKEN"\n')
    (mine / ".nvm").mkdir()
    (mine / ".nvm" / ".npmrc").write_text('_authToken=NOT-A-REAL-TOKEN\n')
    (mine / "Documents").mkdir()
    (mine / "Documents" / "private").write_text("NOT-A-REAL-SECRET\n")
    out, _, _ = shelled(box, f"""\
        tried installed 'installed-tool | grep -q "installed-tool ran"'
        tried installed-under-its-own-home '"$HOME/.cargo/bin/installed-tool"'
        tried change-installed 'echo x >> "$HOME/.cargo/bin/installed-tool"'
        tried registry-credential 'cat {mine}/.cargo/credentials.toml'
        tried tool-credential 'cat "$HOME/.nvm/.npmrc"'
        tried private-file 'cat {mine}/Documents/private'
        tried keychain-list '/usr/bin/security list-keychains'
        tried keychain-item '/usr/bin/security find-generic-password -s poolhouse-proof'
        tried clipboard-read 'test -n "$(/usr/bin/pbpaste)"'
        tried clipboard-write 'echo x | /usr/bin/pbcopy'
        tried preferences-read '/usr/bin/defaults read com.apple.finder'
        tried preferences-write '/usr/bin/defaults write com.example.poolhouse-planted k v'
        """)
    expect(out, ("installed", "installed-under-its-own-home"),
           ("change-installed", "registry-credential", "tool-credential", "private-file", "keychain-list", "keychain-item", "clipboard-read",
            "clipboard-write", "preferences-read", "preferences-write"))
    assert "NOT-A-REAL" not in out, out


def test_an_open_box_reaches_this_machines_loopback_and_nothing_beyond_it_and_binds_no_address(box, earlier):  # noqa: F811
    service = socket.create_server(("127.0.0.1", 0))
    port = service.getsockname()[1]
    try:
        out, _, confinement_ = shelled(box, f"""\
            tried loopback-service {run(f"import socket; socket.create_connection(('127.0.0.1', {port}), 5)")}
            tried loopback-datagram {run("import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('127.0.0.1', 9))")}
            tried model-server-port {run(f"import socket; socket.create_connection(('127.0.0.1', {box.port}), 5)")}
            tried model-server-port-six {run(f"import socket; socket.create_connection(('localhost', {box.port}), 5)")}
            tried beyond {run("import socket; socket.create_connection(('192.0.2.1', 443), 5)")}
            tried beyond-datagram {run("import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('192.0.2.1', 9))")}
            tried names {run("import socket; socket.getaddrinfo('example.invalid.poolhouse.test', 80)")}
            tried resolver-socket {run("import socket; socket.socket(socket.AF_UNIX).connect('/private/var/run/mDNSResponder')")}
            tried others-socket {run(f"import socket; socket.socket(socket.AF_UNIX).connect({str(earlier / 's')!r})")}
            tried bind-loopback {run("import _socket; _socket.socket().bind(('127.0.0.1', 0))")}
            tried bind-everywhere {run("import _socket; _socket.socket().bind(('0.0.0.0', 0))")}
            tried bind-datagram {run("import _socket; _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM).bind(('127.0.0.1', 0))")}
            tried listen-unbound {run("import _socket; _socket.socket().listen(1)")}
            """)
    finally:
        service.close()
    expect(out, ("loopback-service", "loopback-datagram"),
           ("model-server-port", "model-server-port-six", "beyond", "beyond-datagram", "names", "resolver-socket", "others-socket",
            "bind-loopback", "bind-everywhere", "bind-datagram", "listen-unbound"))
    assert box.port in confinement_.profile.policy.net.refused


def test_every_rule_for_a_socket_stands_before_the_refusal_of_the_model_servers_port(box):  # noqa: F811
    """An allow of a socket's path written after the refusal of a loopback port can answer for that port, the model server's among them."""
    namespace = Path(tempfile.mkdtemp(prefix="pp-", dir="/tmp")).resolve()   # where a harness's own server listens (`Spec.server`)
    try:
        _, (text, _), _ = shelled(box, "true\n", look=lambda running: Path(running.profile.stage).is_dir() and seatbelt_open.profile(
            replace(running.profile.policy, unix_namespaces=(str(namespace),)), "/bin/sh", "t"))
    finally:
        namespace.rmdir()
    assert str(namespace) in text and text.rindex(" unix-socket ") < text.index(f'(deny network-outbound (remote ip "localhost:{box.port}")'), text


def test_an_open_box_signals_traces_and_reads_its_own_processes_and_no_other(box):  # noqa: F811
    outsider = subprocess.Popen([os.path.realpath(sys.executable), "-c", "import time; time.sleep(120)", "PLANTED-ARGUMENT"],
                                env={"PLANTED_VARIABLE": "NOT-A-REAL-SECRET"})
    master, slave = os.openpty()
    (box.project / "confinement_copy.py").write_text(Path(confinement.__file__).read_text())
    arguments = ("import ctypes, sys\nlibc = ctypes.CDLL(None)\nmib = (ctypes.c_int * 3)(1, 49, int(sys.argv[1]))\n"
                 "size = ctypes.c_size_t(1 << 20)\nbuffer = ctypes.create_string_buffer(size.value)\n"
                 "sys.exit(1 if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) else 0)\n")
    (box.project / "arguments.py").write_text(arguments)
    try:
        out, _, _ = shelled(box, f"""\
            sleep 30 & own=$!
            tried own-arguments "{PYTHON} -I arguments.py $own"
            tried own-signal "kill -0 $own && kill $own"
            tried own-terminal {run("import os; a, b = os.openpty(); os.write(a, b'x'); os.read(b, 0); os.close(os.open(os.ttyname(b), os.O_RDWR))")}
            tried arguments-outside "{PYTHON} -I arguments.py {outsider.pid}"
            tried arguments-of-the-launcher "{PYTHON} -I arguments.py {os.getpid()}"
            tried signal-outside 'kill -0 {outsider.pid}'
            tried kill-outside 'kill -9 {outsider.pid}'
            tried trace-outside {run(f"import ctypes, sys; sys.exit(0 if ctypes.CDLL(None).ptrace(10, {outsider.pid}, None, None) == 0 else 1)")}
            tried terminal-outside 'exec 3<>{os.ttyname(slave)}'
            tried terminal-read 'exec 3<{os.ttyname(slave)}'
            tried launchd '/bin/launchctl submit -l poolhouse.planted -- /usr/bin/true'
            tried launch-services '/usr/bin/open -g -a TextEdit'
            tried apple-events '/usr/bin/osascript -e "tell application \\"Finder\\" to count windows"'
            tried nested "/usr/bin/sandbox-exec -p '(version 1)(allow default)' /usr/bin/true"
            tried confined {run("import os, sys; sys.path.insert(0, os.getcwd()); import confinement_copy; sys.exit(0 if confinement_copy.confined() else 1)")}
            /usr/sbin/ioreg -rd1 -c IOPlatformExpertDevice | grep -cE 'IOPlatformSerialNumber|IOPlatformUUID' | sed 's/^/identifiers=/'
            """)
        assert outsider.poll() is None, out
    finally:
        outsider.kill()
        outsider.wait()
        os.close(master)
        os.close(slave)
    expect(out, ("own-arguments", "own-signal", "own-terminal", "confined"),
           ("arguments-outside", "arguments-of-the-launcher", "signal-outside", "kill-outside", "trace-outside", "terminal-outside",
            "terminal-read", "launchd", "launch-services", "apple-events", "nested"))
    assert "identifiers=0" in out, out


METAL = r"""
#import <Metal/Metal.h>
int main(void) {
    @autoreleasepool {
        NSError *why = nil;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
            "kernel void odd(device uint *v [[buffer(0)]], uint i [[thread_position_in_grid]]) { v[i] = v[i] * 2 + 1; }";
        id<MTLLibrary> library = [device newLibraryWithSource:source options:nil error:&why];
        id<MTLComputePipelineState> pipeline = [device newComputePipelineStateWithFunction:[library newFunctionWithName:@"odd"] error:&why];
        if (!pipeline) { printf("no pipeline: %s\n", why.description.UTF8String); return 1; }
        uint values[64];
        for (uint i = 0; i < 64; i++) values[i] = i;
        id<MTLBuffer> buffer = [device newBufferWithBytes:values length:sizeof values options:MTLResourceStorageModeShared];
        id<MTLCommandBuffer> commands = [[device newCommandQueue] commandBuffer];
        id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
        [encoder setComputePipelineState:pipeline];
        [encoder setBuffer:buffer offset:0 atIndex:0];
        [encoder dispatchThreads:MTLSizeMake(64, 1, 1) threadsPerThreadgroup:MTLSizeMake(8, 1, 1)];
        [encoder endEncoding];
        [commands commit];
        [commands waitUntilCompleted];
        uint sum = 0;
        for (uint i = 0; i < 64; i++) sum += ((uint *)buffer.contents)[i];
        printf("the GPU summed %u\n", sum);
        return sum == 4096 ? 0 : 1;
    }
}
"""


def test_a_metal_computation_is_compiled_and_run_in_the_box(box):  # noqa: F811
    (box.project / "odd.m").write_text(METAL)
    out, _, _ = shelled(box, """\
        clang -fobjc-arc -framework Metal -framework Foundation -o "$TMPDIR/odd" odd.m 2>&1 | tail -n 5
        "$TMPDIR/odd" 2>&1 | tail -n 5
        """)
    assert "the GPU summed 4096" in out, out


PAGE = """\
import http.server, threading
from playwright.sync_api import sync_playwright


class Page(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<p id=x></p><script>document.getElementById('x').textContent = 'PLUMBING-' + (6 * 7)</script>")


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
threading.Thread(target=server.serve_forever, daemon=True).start()
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page()
    page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
    print("the page says", page.inner_text("#x"))
    browser.close()
server.shutdown()
"""


def test_a_browser_test_runs_in_the_box_on_the_installed_browser(box, monkeypatch):  # noqa: F811
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("this interpreter has no Playwright")
    mine = Path(pwd.getpwuid(os.getuid()).pw_dir)   # the suite moves HOME; what the account installed stays where it is
    places = {place: os.path.realpath(mine / place) for place in toolchain.HOME_TOOLS if (mine / place).is_dir()}
    if "Library/Caches/ms-playwright" not in places:
        pytest.skip(f"no Playwright browser is installed under {mine}")
    monkeypatch.setattr(toolchain, "installed", lambda: places)
    (box.project / "page.py").write_text(PAGE)
    out, _, _ = shelled(box, f"""\
        {shlex.quote(sys.executable)} page.py 2>&1 | grep -v '0x0000' | tail -n 20
        tried write-browser 'touch "$HOME/Library/Caches/ms-playwright/planted"'
        """)
    assert "the page says PLUMBING-42" in out and "shut write-browser" in out, out


def test_a_deny_list_of_another_program_is_no_box_of_the_engines():
    """The mark an open box refuses is what tells it from a sandbox that allows by default and is not the engine's, such as the test runner's."""
    other = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", '(version 1)(allow default)(deny file-write* (subpath "/private/var/empty"))', "/bin/cat"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        other.stdin.write(b"ready\n")
        other.stdin.flush()
        assert other.stdout.readline() == b"ready\n"
        assert confinement.seatbelt_state(other.pid) == "allows" and confinement.seatbelt_state(os.getpid()) == "none"
    finally:
        other.kill()
        other.wait()
        other.stdin.close()
        other.stdout.close()
