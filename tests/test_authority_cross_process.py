"""Verify actual distinct-process XPC sender checks in a private test bundle."""

import plistlib
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from tests.test_authority_channel import build_probe
from tests.test_mach_sender import _execute

SOURCE = r'''
/* This fixture exchanges OS-observed session numbers through its private temp
 * directory solely to exercise different processes. Those files and the raw
 * probe are not protected policy, authentication, or a product bootstrap. */
#include "AuthorityChannel.h"
#include <bsm/libbsm.h>
#include <dispatch/dispatch.h>
#include <libproc.h>
#include <mach/task_info.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
extern void channel_probe_link(void);

static bool make_policy(const char *path, ph_native_xpc_caller_policy *policy) {
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL,(const UInt8 *)path,(CFIndex)strlen(path),false);
    SecStaticCodeRef code = NULL; SecRequirementRef requirement = NULL;
    audit_token_t token = {0}; mach_msg_type_number_t words = TASK_AUDIT_TOKEN_COUNT;
    if (!url || SecStaticCodeCreateWithPath(url,kSecCSDefaultFlags,&code) || !code ||
        SecCodeCopyDesignatedRequirement(code,kSecCSDefaultFlags,&requirement) || !requirement ||
        !ph_native_identity_observe(code,&policy->identity) ||
        task_info(mach_task_self(),TASK_AUDIT_TOKEN,(task_info_t)&token,&words) != KERN_SUCCESS || words != TASK_AUDIT_TOKEN_COUNT) return false;
    policy->requirement = requirement; policy->euid = audit_token_to_euid(token); policy->asid = audit_token_to_asid(token);
    CFRelease(code); CFRelease(url); return true;
}
static bool exchange_path(const char *path, const char *name, char output[PROC_PIDPATHINFO_MAXSIZE]) {
    if (strlcpy(output,path,PROC_PIDPATHINFO_MAXSIZE) >= PROC_PIDPATHINFO_MAXSIZE) return false;
    char *suffix = strstr(output,"/Fixture.app/");
    if (!suffix) return false;
    *suffix = 0;
    return strlcat(output,name,PROC_PIDPATHINFO_MAXSIZE) < PROC_PIDPATHINFO_MAXSIZE;
}

#ifdef SERVICE
static ph_native_xpc_caller_policy policy;
static FILE *trace;
static xpc_connection_t probe;
static bool silent_probe;
static void handle(void *unused, ph_authority_channel *channel, ph_channel_event event, const uint8_t *frame, size_t length) {
    (void)unused;
    if (trace) { fprintf(trace,"event %d\n",event); fflush(trace); }
    if (event == PH_CHANNEL_FRAME && length == 38 && frame[5] == 1) {
        uint8_t bound[330] = {'P','H','A','C',2,2}; memset(bound+6,1,288);
        memset(bound+38,2,32); bound[301]=1; bound[309]=1; bound[317]=1; bound[321]=1; bound[325]=1;
        if (!ph_authority_channel_send(channel,bound,sizeof(bound))) ph_authority_channel_close(channel);
    } else if (event == PH_CHANNEL_FRAME && length == 38 && frame[5] == 3) {
        ph_authority_channel_close(channel);
    } else if (event == PH_CHANNEL_DISPOSED) { _exit(0); }
}
static void accept_service(xpc_connection_t connection) {
    if (!probe) {
        probe = connection; xpc_retain(probe);
        xpc_connection_set_event_handler(probe, ^(xpc_object_t event) {
            if (xpc_get_type(event) == XPC_TYPE_ERROR) _exit(0);
            if (xpc_get_type(event) == XPC_TYPE_DICTIONARY && !silent_probe) {
                xpc_object_t reply = xpc_dictionary_create(NULL,NULL,0);
                xpc_dictionary_set_bool(reply,"observed",true);
                xpc_connection_send_message(probe,reply); xpc_release(reply);
            }
        });
        xpc_connection_activate(probe);
        return;
    }
    if (!ph_authority_channel_accept(connection,&policy,handle,NULL)) _exit(2);
}
int main(void) {
    channel_probe_link();
    char path[PROC_PIDPATHINFO_MAXSIZE];
    if (proc_pidpath(getpid(),path,sizeof(path)) <= 0) return 3;
    char owner_path[PROC_PIDPATHINFO_MAXSIZE], service_path[PROC_PIDPATHINFO_MAXSIZE];
    if (!exchange_path(path,"/fixture-owner",owner_path) || !exchange_path(path,"/fixture-service",service_path)) return 16;
    char *suffix = strstr(path,"/Contents/XPCServices/");
    if (!suffix) return 4;
    *suffix = 0;
    char logpath[PROC_PIDPATHINFO_MAXSIZE];
    if (strlcpy(logpath,path,sizeof(logpath)) < sizeof(logpath)) {
        char *last = strrchr(logpath,'/');
        if (last) { *last = 0; if (strlcat(logpath,"/service-trace",sizeof(logpath)) < sizeof(logpath)) {
            trace = fopen(logpath,"a"); (void)freopen(logpath,"a",stderr);
        } }
    }
    if (strlcat(path,"/Contents/MacOS/Client",sizeof(path)) >= sizeof(path) || !make_policy(path,&policy)) return 5;
    FILE *owner = fopen(owner_path,"r"); unsigned owner_asid = 0;
    if (!owner || fscanf(owner,"%u",&owner_asid) != 1 || fclose(owner)) return 17;
    struct proc_bsdinfo birth = {0};
    if (proc_pidinfo(getpid(),PROC_PIDTBSDINFO,0,&birth,sizeof(birth)) != sizeof(birth) || !birth.pbi_start_tvsec) return 18;
    int service_fd = open(service_path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    FILE *service = service_fd < 0 ? NULL : fdopen(service_fd,"w");
    if (!service || fprintf(service,"%u %d %llu %llu\n",policy.asid,getpid(),(unsigned long long)birth.pbi_start_tvsec,(unsigned long long)birth.pbi_start_tvusec) <= 0 || fclose(service)) return 18;
    policy.asid = owner_asid;
    if (trace) { fputs("policy ready\n",trace); fflush(trace); }
    char fail_path[PROC_PIDPATHINFO_MAXSIZE];
    if (!exchange_path(path,"/fixture-fail-start",fail_path)) return 33;
    if (access(fail_path,F_OK) == 0) return 34;
    if (!exchange_path(path,"/fixture-silent",fail_path)) return 36;
    silent_probe = access(fail_path,F_OK) == 0;
    xpc_main(accept_service);
    return 6;
}
#else
struct state { dispatch_semaphore_t done; const char *service; pid_t pid; struct proc_bsdinfo birth; int error; int end_error; };
static void handle(void *raw, ph_authority_channel *channel, ph_channel_event event, const uint8_t *frame, size_t length) {
    struct state *state = raw;
    if (event == PH_CHANNEL_START) {
        uint8_t open[38] = {'P','H','A','C',2,1}; memset(open+6,1,32);
        if (!ph_authority_channel_send(channel,open,sizeof(open))) state->error = 7;
    } else if (event == PH_CHANNEL_FRAME && length == 330 && frame[5] == 2) {
        state->pid = ph_authority_channel_test_pid(channel);
        char actual[PROC_PIDPATHINFO_MAXSIZE];
        if (state->pid <= 0 || state->pid == getpid() || proc_pidpath(state->pid,actual,sizeof(actual)) <= 0 ||
            strcmp(actual,state->service) || proc_pidinfo(state->pid,PROC_PIDTBSDINFO,0,&state->birth,sizeof(state->birth)) != sizeof(state->birth)) {
            state->error = 8; ph_authority_channel_close(channel); return;
        }
        uint8_t confirm[38] = {'P','H','A','C',2,3}; memset(confirm+6,3,32);
        if (!ph_authority_channel_send(channel,confirm,sizeof(confirm))) state->error = 9;
    } else if (event == PH_CHANNEL_END) { state->end_error = ph_authority_channel_test_error(channel); }
    else if (event == PH_CHANNEL_DISPOSED) { dispatch_semaphore_signal(state->done); }
}
int main(int argc, char **argv) {
    if (argc == 5 && !strcmp(argv[1],"--wait")) {
        pid_t pid = (pid_t)strtol(argv[2],NULL,10);
        unsigned long long seconds = strtoull(argv[3],NULL,10), microseconds = strtoull(argv[4],NULL,10);
        if (pid <= 0 || !seconds) return 23;
        for (int i=0; i<100; i++) {
            struct proc_bsdinfo now = {0};
            int size = proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&now,sizeof(now));
            if (size == sizeof(now) && (now.pbi_start_tvsec != seconds || now.pbi_start_tvusec != microseconds)) return 0;
            if (size != sizeof(now) && kill(pid,0) == -1 && errno == ESRCH) return 0;
            struct timespec delay = { .tv_nsec=50000000 }; nanosleep(&delay,NULL);
        }
        return 24;
    }
    int wrong = argc == 4 && !strcmp(argv[3],"--wrong-requirement");
    int fail_start = argc == 4 && !strcmp(argv[3],"--fail-start");
    int timeout = argc == 4 && !strcmp(argv[3],"--silent");
    if (argc != 3 && !wrong && !fail_start && !timeout) return 10;
    channel_probe_link();
    char owner_path[PROC_PIDPATHINFO_MAXSIZE], service_path[PROC_PIDPATHINFO_MAXSIZE];
    if (!exchange_path(argv[2],"/fixture-owner",owner_path) || !exchange_path(argv[2],"/fixture-service",service_path)) return 19;
    ph_native_xpc_caller_policy policy = {0};
    if (!make_policy(argv[2],&policy)) return 11;
    if (wrong) {
        SecRequirementRef rejected = NULL;
        if (SecRequirementCreateWithString(CFSTR("identifier \"invalid.poolhouse.fixture\""),kSecCSDefaultFlags,&rejected) || !rejected) return 27;
        CFRelease(policy.requirement); policy.requirement = rejected;
    }
    FILE *owner = fopen(owner_path,"w");
    if (!owner || fprintf(owner,"%u\n",policy.asid) <= 0 || fclose(owner)) return 20;
    xpc_connection_t probe = xpc_connection_create(argv[1],NULL);
    if (!probe) return 21;
    dispatch_semaphore_t probe_done = dispatch_semaphore_create(0);
    __block bool probe_ok = false;
    xpc_connection_set_event_handler(probe,^(xpc_object_t event) {
        probe_ok = xpc_get_type(event) == XPC_TYPE_DICTIONARY && xpc_dictionary_get_bool(event,"observed");
        dispatch_semaphore_signal(probe_done);
    });
    xpc_connection_activate(probe);
    xpc_object_t ping = xpc_dictionary_create(NULL,NULL,0);
    xpc_connection_send_message(probe,ping); xpc_release(ping);
    if (dispatch_semaphore_wait(probe_done,dispatch_time(DISPATCH_TIME_NOW,(timeout ? 1 : 10)*NSEC_PER_SEC))) {
        xpc_connection_cancel(probe); xpc_release(probe);
        if (timeout) { puts("silent helper timeout observed"); return 0; }
        return 31;
    }
    if (timeout) return 37;
    if (fail_start) {
        xpc_connection_cancel(probe); xpc_release(probe);
        if (probe_ok) return 35;
        puts("failed helper startup observed"); return 0;
    }
    if (!probe_ok) return 31;
    unsigned service_asid = 0; int service_pid = 0; unsigned long long seconds = 0, microseconds = 0;
    for (int i=0; i<100; i++) {
        FILE *file = fopen(service_path,"r");
        if (file) { int read = fscanf(file,"%u %d %llu %llu",&service_asid,&service_pid,&seconds,&microseconds); fclose(file); if (read == 4) break; }
        struct timespec delay = { .tv_nsec=50000000 }; nanosleep(&delay,NULL);
    }
    if (service_pid <= 0 || !seconds || xpc_connection_get_pid(probe) != service_pid ||
        (uint32_t)xpc_connection_get_asid(probe) != service_asid) return 22;
    char probe_path[PROC_PIDPATHINFO_MAXSIZE]; struct proc_bsdinfo probe_birth = {0};
    if (proc_pidpath(service_pid,probe_path,sizeof(probe_path)) <= 0 || strcmp(probe_path,argv[2]) ||
        proc_pidinfo(service_pid,PROC_PIDTBSDINFO,0,&probe_birth,sizeof(probe_birth)) != sizeof(probe_birth) ||
        probe_birth.pbi_start_tvsec != seconds || probe_birth.pbi_start_tvusec != microseconds) return 32;
    policy.asid = service_asid;
    struct state state = { .done=dispatch_semaphore_create(0), .service=argv[2] };
    if (!ph_authority_channel_test_service(argv[1],&policy,handle,&state)) return 12;
    if (dispatch_semaphore_wait(state.done,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC))) return 13;
    xpc_connection_cancel(probe); xpc_release(probe);
    if (wrong) {
        if (state.error || state.pid || state.end_error != 1) return 28;
        state.pid = service_pid; state.birth.pbi_start_tvsec = seconds; state.birth.pbi_start_tvusec = microseconds;
    } else if (state.error || state.pid <= 0 || state.pid != service_pid || state.birth.pbi_start_tvsec != seconds || state.birth.pbi_start_tvusec != microseconds) { fprintf(stderr,"state error=%d pid=%d\n",state.error,state.pid); return 14; }
    for (int i=0; i<100; i++) {
        struct proc_bsdinfo now = {0};
        int size = proc_pidinfo(state.pid,PROC_PIDTBSDINFO,0,&now,sizeof(now));
        if ((size == sizeof(now) && (now.pbi_start_tvsec != state.birth.pbi_start_tvsec || now.pbi_start_tvusec != state.birth.pbi_start_tvusec)) ||
            (size != sizeof(now) && kill(state.pid,0) == -1 && errno == ESRCH)) {
            puts(wrong ? "wrong service requirement rejected and helper exited" : "separate XPC sender verified and exited"); return 0;
        }
        struct timespec delay = { .tv_nsec=50000000 }; nanosleep(&delay,NULL);
    }
    return 15;
}
#endif
'''


def make_bundle(fixture_root, name, mode):
    app = fixture_root / "Fixture.app" / "Contents"
    service = app / "XPCServices" / "Fixture.xpc" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (service / "MacOS").mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":name+".client", "CFBundleExecutable":"Client", "CFBundlePackageType":"APPL", "CFBundleVersion":"1"}))
    (service / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":name, "CFBundleExecutable":"Service", "CFBundlePackageType":"XPC!", "CFBundleVersion":"1", "XPCService":{"ServiceType":"Application"}}))
    if mode == "fail-start":
        (fixture_root / "fixture-fail-start").write_text("test-only failure")
    elif mode == "silent":
        (fixture_root / "fixture-silent").write_text("test-only silence")
    return app, service


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS XPC SDK")
@pytest.mark.timeout(600)
@pytest.mark.parametrize("mode", ["normal", "wrong", "fail-start", "silent"])
def test_distinct_process_authority_connection(tmp_path, mode):
    build, _environment, archive = build_probe(tmp_path, "channel_cross_probe")
    fixture_root = Path(tempfile.mkdtemp(prefix="poolhouse-xpc-fixture-")).resolve()
    fixture_environment = {"PATH":"/usr/bin:/bin", "HOME":str(fixture_root), "TMPDIR":str(fixture_root), "LANG":"C"}
    source = tmp_path / "cross.c"
    source.write_text(SOURCE)
    name = "app.poolhouse.fixture." + uuid.uuid4().hex
    app, service = make_bundle(fixture_root,name,mode)
    compiler = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
    sdk = "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
    native = build.staged / "native-host"
    common = [compiler,"-isysroot",sdk,"-fno-modules","-fno-implicit-modules","-fblocks","-std=c11",
              "-Wall","-Wextra","-Werror","-DTESTING","-I",str(native)]
    try:
        for label, output, flags in [("service",service / "MacOS/Service",["-DSERVICE"]), ("client",app / "MacOS/Client",[])]:
            _execute([*common,*flags,str(source),str(native / "AuthorityChannel.c"),str(archive),"-lbsm",
                      "-framework","Security","-framework","CoreFoundation","-o",str(output)],
                     tmp_path,"compile-"+label,fixture_environment,(120,67108864))
        _execute(["/usr/bin/codesign","--force","--sign","-","--options","runtime",str(service.parent)],tmp_path,"sign-service",fixture_environment,(30,67108864))
        _execute(["/usr/bin/codesign","--force","--sign","-","--options","runtime",str(app.parent)],tmp_path,"sign-app",fixture_environment,(30,67108864))
    except BaseException:
        # The test service has not been started yet.
        shutil.rmtree(fixture_root)
        raise
    try:
        args = [str(app / "MacOS/Client"),name,str(service / "MacOS/Service")]
        if mode == "wrong":
            args.append("--wrong-requirement")
        elif mode == "fail-start":
            args.append("--fail-start")
        elif mode == "silent":
            args.append("--silent")
        out, err = _execute(args,tmp_path,"run-cross",fixture_environment,(25,65536))
    except AssertionError as error:
        trace = fixture_root / "service-trace"
        raise AssertionError(f"{error}; isolated helper trace: {trace.read_text() if trace.exists() else 'not started'}") from error
    finally:
        # XPC app services are per-client and tied to the client lifetime:
        # https://developer.apple.com/documentation/xpc. This checks the one
        # test-owned process observed by the probe, not general launchd state.
        marker = fixture_root / "fixture-service"
        parts = marker.read_text().split() if marker.exists() and marker.stat().st_size <= 256 else []
        if len(parts) != 4 or not all(part.isascii() and part.isdecimal() and len(part) <= 20 for part in parts) or int(parts[1]) == 0 or int(parts[2]) == 0:
            raise AssertionError(f"Isolated XPC helper identity is unresolved; fixture preserved at {fixture_root}")
        try:
            _execute([str(app / "MacOS/Client"),"--wait",parts[1],parts[2],parts[3]],tmp_path,
                     "wait-helper",fixture_environment,(10,65536))
        except AssertionError as cleanup_error:
            raise AssertionError(f"Isolated XPC helper did not settle; fixture preserved at {fixture_root}") from cleanup_error
        else:
            shutil.rmtree(fixture_root)
    assert out == {"normal":"separate XPC sender verified and exited\n", "wrong":"wrong service requirement rejected and helper exited\n", "fail-start":"failed helper startup observed\n", "silent":"silent helper timeout observed\n"}[mode]
    assert not err
