"""Exercise the single native XPC owner against isolated OS connections."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode

from tests.test_mach_sender import _execute

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
#include "AuthorityChannel.h"
#include <bsm/libbsm.h>
#include <dispatch/dispatch.h>
#include <mach/task_info.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
extern void listener_probe_link(void);

struct context { int node; int frames; int ended; int disposed; int error; dispatch_semaphore_t done; };
struct fixture { struct context node; struct context server; ph_native_xpc_caller_policy node_policy;
    int active_stop; int listener_ended; int listener_disposed; int error; dispatch_semaphore_t done; };
static void *off_queue_stop(void *raw) { ph_authority_session_stop_listener(raw); return NULL; }
static void callback(void *raw, ph_authority_channel *channel, ph_channel_event event, const uint8_t *frame, size_t length) {
    struct context *c = raw;
    if (event == PH_CHANNEL_START && c->node) {
        uint8_t open[38] = {'P','H','A','C',1,1}; memset(open+6,1,32);
        uint8_t malformed[38] = {0};
        if (ph_authority_channel_send(channel,malformed,sizeof(malformed))) c->error = 6;
        if (!ph_authority_channel_send(channel,open,sizeof(open))) c->error = 1;
    } else if (event == PH_CHANNEL_FRAME) {
        c->frames++;
        if (!c->node && length == 38 && frame[5] == 1) {
            uint8_t bound[326] = {'P','H','A','C',1,2}; memset(bound+6,1,288);
            memset(bound+38,2,32); bound[301]=1; bound[309]=1; bound[317]=1; bound[321]=1; bound[325]=1;
            if (!ph_authority_channel_send(channel,bound,sizeof(bound))) c->error = 2;
        } else if (c->node && length == 326 && frame[5] == 2) {
            uint8_t confirm[38] = {'P','H','A','C',1,3}; memset(confirm+6,3,32);
            if (!ph_authority_channel_send(channel,confirm,sizeof(confirm))) c->error = 3;
        } else if (!c->node && length == 38 && frame[5] == 3) {
            ph_authority_channel_close(channel);
        } else { c->error = 4; ph_authority_channel_close(channel); }
    } else if (event == PH_CHANNEL_END) {
        c->ended++;
        ph_authority_channel_close(channel); /* reentrant close is harmless */
    } else if (event == PH_CHANNEL_DISPOSED) {
        c->disposed++; dispatch_semaphore_signal(c->done);
    }
}
static void session_callback(void *raw, ph_authority_session *session, ph_authority_channel *channel,
    ph_channel_event event, const uint8_t *frame, size_t length) {
    struct fixture *fixture = raw;
    if (event == PH_CHANNEL_START) {
        pthread_t worker;
        if (pthread_create(&worker,NULL,off_queue_stop,session) || pthread_join(worker,NULL)) fixture->error = 8;
    }
    if (fixture->active_stop && event == PH_CHANNEL_FRAME && length == 38 && frame[5] == 1) {
        fixture->server.frames++;
        ph_authority_session_stop_listener(session);
        return;
    }
    callback(&fixture->server,channel,event,frame,length);
    if (event == PH_CHANNEL_END) ph_authority_session_stop_listener(session);
}
static void listener_callback(void *raw, ph_authority_listener *listener, ph_listener_event event) {
    struct fixture *fixture = raw;
    if (event == PH_LISTENER_START) {
        xpc_endpoint_t endpoint = ph_authority_listener_test_endpoint(listener);
        if (!endpoint || !ph_authority_channel_test_connect(endpoint,&fixture->node_policy,callback,&fixture->node)) {
            fixture->error = 7;
            ph_authority_listener_stop(listener);
        }
        if (endpoint) xpc_release(endpoint);
    } else if (event == PH_LISTENER_END) {
        fixture->listener_ended++;
        ph_authority_listener_stop(listener); /* reentrant stop is harmless */
    } else if (event == PH_LISTENER_DISPOSED) {
        fixture->listener_disposed++;
        dispatch_semaphore_signal(fixture->done);
    }
}
int main(int argc, char **argv) {
    int wrong = argc == 2 && !strcmp(argv[1],"--wrong-session");
    int active = argc == 2 && !strcmp(argv[1],"--active-stop");
    if (argc != 1 && !wrong && !active) return 6;
    listener_probe_link();
    SecCodeRef self = NULL; SecRequirementRef requirement = NULL;
    ph_native_xpc_caller_policy policy = {0};
    audit_token_t token = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
    if (SecCodeCopySelf(kSecCSDefaultFlags,&self) || !self ||
        SecCodeCopyDesignatedRequirement(self,kSecCSDefaultFlags,&requirement) || !requirement ||
        !ph_native_identity_observe(self,&policy.identity) ||
        task_info(mach_task_self(),TASK_AUDIT_TOKEN,(task_info_t)&token,&words) != KERN_SUCCESS || words != TASK_AUDIT_TOKEN_COUNT) return 1;
    policy.requirement = requirement; policy.euid = audit_token_to_euid(token); policy.asid = audit_token_to_asid(token);
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    struct fixture fixture = { .node = { .node = 1, .done = done }, .server = { .done = done },
        .node_policy = policy, .active_stop = active, .done = done };
    if (wrong) fixture.node_policy.asid ^= 1;
    if (!ph_authority_listener_test_start(&policy,listener_callback,session_callback,&fixture)) return 3;
    if (dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)) ||
        dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)) ||
        dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC))) return 4;
    if (fixture.error || fixture.node.error || fixture.server.error || fixture.node.frames != ((wrong || active) ? 0 : 1) || fixture.server.frames != ((wrong || active) ? 1 : 2) ||
        fixture.node.ended != 1 || fixture.server.ended != 1 || fixture.node.disposed != 1 || fixture.server.disposed != 1 ||
        fixture.listener_ended != 1 || fixture.listener_disposed != 1) {
        fprintf(stderr,"node %d/%d/%d/%d server %d/%d/%d/%d listener %d/%d/%d\n",fixture.node.error,fixture.node.frames,fixture.node.ended,fixture.node.disposed,
            fixture.server.error,fixture.server.frames,fixture.server.ended,fixture.server.disposed,fixture.error,fixture.listener_ended,fixture.listener_disposed); return 5;
    }
    puts(wrong ? "wrong session closes both channels" : active ? "active listener stops and drains" : "serial authority channels drain their callbacks");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS XPC SDK")
@pytest.mark.timeout(600)
def test_authority_listener_lifetime(tmp_path):
    build = testnode.shared_build(ROOT / "app")
    environment = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                   "RUSTUP_HOME": str(testnode.account() / ".rustup"), "CARGO_HOME": str(testnode.account() / ".cargo"),
                   "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1"}
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "Cargo.toml").write_text('[package]\nname="listener_probe"\nversion="0.1.0"\nedition="2021"\n'
        '[lib]\ncrate-type=["staticlib"]\n[dependencies]\npoolhouse-authority={path='
        + json.dumps(str(build.staged / "poolhouse-authority")) + '}\n')
    (project / "src/lib.rs").write_text('#[no_mangle] pub extern "C" fn listener_probe_link() {'
        'let bytes = [0u8; 38]; unsafe { let _ = poolhouse_authority::channel_wire::ph_authority_frame_kind(bytes.as_ptr(), bytes.len()); }}\n')
    original_lock = (build.staged / "Cargo.lock").read_bytes()
    (project / "Cargo.lock").write_bytes(original_lock)
    generated = subprocess.run([testnode.tool("cargo"), "metadata", "--offline", "--format-version", "1"], cwd=project,
                               env=environment, capture_output=True, text=True, timeout=600)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    def locked_packages(raw):
        return {(p["name"], p["version"], p.get("source"), p.get("checksum"))
                for p in tomllib.loads(raw.decode())["package"] if p["name"] != "listener_probe"}
    assert locked_packages((project / "Cargo.lock").read_bytes()) <= locked_packages(original_lock)
    result = subprocess.run([testnode.tool("cargo"), "build", "--offline", "--locked"], cwd=project,
                            env=environment, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    archive = build.target / "debug/liblistener_probe.a"
    assert archive.is_file()
    symbols_file = tmp_path / "production-symbols"
    with symbols_file.open("wb") as symbols:
        checked = subprocess.run(["/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/nm",
                                  "-g", str(archive)], stdout=symbols, stderr=subprocess.PIPE, env=environment, timeout=60)
    assert checked.returncode == 0, checked.stderr.decode(errors="replace")
    assert symbols_file.stat().st_size <= 32 * 1024 * 1024
    assert b"ph_authority_channel_test_connect" not in symbols_file.read_bytes()
    assert b"ph_authority_listener_test_start" not in symbols_file.read_bytes()
    assert b"ph_authority_listener_test_endpoint" not in symbols_file.read_bytes()
    fixture, binary = tmp_path / "fixture.c", tmp_path / "fixture"
    fixture.write_text(SOURCE)
    native = build.staged / "native-host"
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    _execute([compiler, "-isysroot", sdk, "-fno-modules", "-fno-implicit-modules", "-fblocks", "-std=c11",
              "-Wall", "-Wextra", "-Werror", "-DTESTING", "-I", str(native), str(fixture),
              str(native / "AuthorityChannel.c"), str(archive), "-lbsm", "-framework", "Security",
              "-framework", "CoreFoundation", "-o", str(binary)], tmp_path, "compile", environment, (120, 67108864))
    _execute(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", str(binary)],
             tmp_path, "sign", environment, (30, 67108864))
    out, err = _execute([str(binary)], tmp_path, "run", environment, (25, 65536))
    assert out == "serial authority channels drain their callbacks\n"
    assert not err
    out, err = _execute([str(binary), "--wrong-session"], tmp_path, "wrong-session", environment, (25, 65536))
    assert out == "wrong session closes both channels\n"
    assert not err
    out, err = _execute([str(binary), "--active-stop"], tmp_path, "active-stop", environment, (25, 65536))
    assert out == "active listener stops and drains\n"
    assert not err
