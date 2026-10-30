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
extern void channel_probe_link(void);

struct context { int node; int idle; int race; int frames; int ended; int disposed; int error; dispatch_semaphore_t done;
    ph_authority_connection_control *retained; };
struct gate { dispatch_semaphore_t entered; dispatch_semaphore_t attempted; dispatch_semaphore_t finished;
    ph_authority_connection_control *control; };
static void hold_send(void *raw) {
    struct gate *gate = raw;
    dispatch_semaphore_signal(gate->entered);
    dispatch_semaphore_wait(gate->attempted,DISPATCH_TIME_FOREVER);
}
static void *cancel_racing(void *raw) {
    struct gate *gate = raw;
    dispatch_semaphore_wait(gate->entered,DISPATCH_TIME_FOREVER);
    dispatch_semaphore_signal(gate->attempted);
    ph_authority_connection_control_cancel(gate->control);
    dispatch_semaphore_signal(gate->finished);
    ph_authority_connection_control_release(gate->control);
    return NULL;
}
static void *cancel_idle(void *raw) {
    ph_authority_connection_control *control = raw;
    ph_authority_connection_control_cancel(control);
    ph_authority_connection_control_cancel(control);
    ph_authority_connection_control_release(control);
    return NULL;
}
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
            if (c->idle) {
                ph_authority_connection_control *control = ph_authority_connection_control_copy(channel);
                c->retained = ph_authority_connection_control_copy(channel);
                pthread_t worker;
                if (!control || !c->retained || pthread_create(&worker,NULL,cancel_idle,control)) { c->error = 7; ph_authority_channel_close(channel); return; }
                pthread_detach(worker);
                return;
            }
            uint8_t bound[326] = {'P','H','A','C',1,2}; memset(bound+6,1,288);
            memset(bound+38,2,32); bound[301]=1; bound[309]=1; bound[317]=1; bound[321]=1; bound[325]=1;
            if (c->race) {
                struct gate gate = { .entered=dispatch_semaphore_create(0), .attempted=dispatch_semaphore_create(0),
                    .finished=dispatch_semaphore_create(0), .control=ph_authority_connection_control_copy(channel) };
                pthread_t worker;
                if (!gate.control || pthread_create(&worker,NULL,cancel_racing,&gate)) { c->error = 8; ph_authority_channel_close(channel); return; }
                ph_authority_channel_test_send_hook(channel,hold_send,&gate);
                if (!ph_authority_channel_send(channel,bound,sizeof(bound))) c->error = 9;
                dispatch_semaphore_wait(gate.finished,DISPATCH_TIME_FOREVER);
                ph_authority_channel_test_send_hook(channel,NULL,NULL);
                if (ph_authority_channel_send(channel,bound,sizeof(bound))) c->error = 10;
                pthread_join(worker,NULL);
                dispatch_release(gate.entered); dispatch_release(gate.attempted); dispatch_release(gate.finished);
                return;
            }
            if (!ph_authority_channel_send(channel,bound,sizeof(bound))) c->error = 2;
        } else if (c->node && length == 326 && frame[5] == 2) {
            uint8_t confirm[38] = {'P','H','A','C',1,3}; memset(confirm+6,3,32);
            if (!ph_authority_channel_send(channel,confirm,sizeof(confirm)) && !c->race) c->error = 3;
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
int main(int argc, char **argv) {
    int wrong = argc == 2 && !strcmp(argv[1],"--wrong-session");
    int idle = argc == 2 && !strcmp(argv[1],"--idle-cancel");
    int race = argc == 2 && !strcmp(argv[1],"--cancel-race");
    if (argc != 1 && !wrong && !idle && !race) return 6;
    channel_probe_link();
    SecCodeRef self = NULL; SecRequirementRef requirement = NULL;
    ph_native_xpc_caller_policy policy = {0};
    audit_token_t token = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
    if (SecCodeCopySelf(kSecCSDefaultFlags,&self) || !self ||
        SecCodeCopyDesignatedRequirement(self,kSecCSDefaultFlags,&requirement) || !requirement ||
        !ph_native_identity_observe(self,&policy.identity) ||
        task_info(mach_task_self(),TASK_AUDIT_TOKEN,(task_info_t)&token,&words) != KERN_SUCCESS || words != TASK_AUDIT_TOKEN_COUNT) return 1;
    policy.requirement = requirement; policy.euid = audit_token_to_euid(token); policy.asid = audit_token_to_asid(token);
    const ph_native_xpc_caller_policy *policy_ptr = &policy;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    struct context server = { .idle = idle, .race = race, .done = done }, node = { .node = 1, .race = race, .done = done };
    struct context *server_ptr = &server;
    dispatch_queue_t listener_queue = dispatch_queue_create("isolated.authority.listener",DISPATCH_QUEUE_SERIAL);
    xpc_connection_t listener = xpc_connection_create(NULL,listener_queue);
    if (!listener) return 2;
    xpc_connection_set_event_handler(listener, ^(xpc_object_t event) {
        if (xpc_get_type(event) == XPC_TYPE_CONNECTION && !ph_authority_channel_accept((xpc_connection_t)event,policy_ptr,callback,server_ptr)) {
            server_ptr->error = 5; dispatch_semaphore_signal(done);
        }
    });
    xpc_connection_activate(listener);
    xpc_endpoint_t endpoint = xpc_endpoint_create(listener);
    ph_native_xpc_caller_policy node_policy = policy;
    if (wrong) node_policy.asid ^= 1;
    if (!endpoint || !ph_authority_channel_test_connect(endpoint,&node_policy,callback,&node)) return 3;
    if (dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)) ||
        dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC))) return 4;
    if (idle) {
        if (!server.retained) return 7;
        ph_authority_connection_control_cancel(server.retained);
        ph_authority_connection_control_cancel(server.retained);
        ph_authority_connection_control_release(server.retained);
    }
    if (node.error || server.error || (!race && node.frames != ((wrong || idle) ? 0 : 1)) || node.frames > 1 || server.frames != ((wrong || idle || race) ? 1 : 2) ||
        node.ended != 1 || server.ended != 1 || node.disposed != 1 || server.disposed != 1) {
        fprintf(stderr,"node %d/%d/%d/%d server %d/%d/%d/%d\n",node.error,node.frames,node.ended,node.disposed,
            server.error,server.frames,server.ended,server.disposed); return 5;
    }
    /* Listener dependencies remain alive through this isolated process exit. */
    puts(wrong ? "wrong session closes both channels" : idle ? "idle channel cancels from another thread" : race ? "cancel fences subsequent sends" : "serial authority channels drain their callbacks");
    return 0;
}
'''


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS XPC SDK")
@pytest.mark.timeout(600)
def test_authority_channel_lifetime(tmp_path):
    build = testnode.shared_build(ROOT / "app")
    environment = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                   "RUSTUP_HOME": str(testnode.account() / ".rustup"), "CARGO_HOME": str(testnode.account() / ".cargo"),
                   "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1"}
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "Cargo.toml").write_text('[package]\nname="channel_probe"\nversion="0.1.0"\nedition="2021"\n'
        '[lib]\ncrate-type=["staticlib"]\n[dependencies]\npoolhouse-authority={path='
        + json.dumps(str(build.staged / "poolhouse-authority")) + '}\n')
    (project / "src/lib.rs").write_text('#[no_mangle] pub extern "C" fn channel_probe_link() {'
        'let bytes = [0u8; 38]; unsafe { let _ = poolhouse_authority::channel_wire::ph_authority_frame_kind(bytes.as_ptr(), bytes.len()); }}\n')
    original_lock = (build.staged / "Cargo.lock").read_bytes()
    (project / "Cargo.lock").write_bytes(original_lock)
    generated = subprocess.run([testnode.tool("cargo"), "metadata", "--offline", "--format-version", "1"], cwd=project,
                               env=environment, capture_output=True, text=True, timeout=600)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    def locked_packages(raw):
        return {(p["name"], p["version"], p.get("source"), p.get("checksum"))
                for p in tomllib.loads(raw.decode())["package"] if p["name"] != "channel_probe"}
    assert locked_packages((project / "Cargo.lock").read_bytes()) <= locked_packages(original_lock)
    result = subprocess.run([testnode.tool("cargo"), "build", "--offline", "--locked"], cwd=project,
                            env=environment, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    archive = build.target / "debug/libchannel_probe.a"
    assert archive.is_file()
    symbols_file = tmp_path / "production-symbols"
    with symbols_file.open("wb") as symbols:
        checked = subprocess.run(["/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/nm",
                                  "-g", str(archive)], stdout=symbols, stderr=subprocess.PIPE, env=environment, timeout=60)
    assert checked.returncode == 0, checked.stderr.decode(errors="replace")
    assert symbols_file.stat().st_size <= 32 * 1024 * 1024
    assert b"ph_authority_channel_test_connect" not in symbols_file.read_bytes()
    assert b"ph_authority_channel_test_send_hook" not in symbols_file.read_bytes()
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
    out, err = _execute([str(binary), "--idle-cancel"], tmp_path, "idle-cancel", environment, (25, 65536))
    assert out == "idle channel cancels from another thread\n"
    assert not err
    for index in range(10):
        out, err = _execute([str(binary), "--cancel-race"], tmp_path, f"cancel-race-{index}", environment, (25, 65536))
        assert out == "cancel fences subsequent sends\n"
        assert not err
