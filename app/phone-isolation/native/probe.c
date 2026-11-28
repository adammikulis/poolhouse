/* What the process that runs this can execute and reach. One line per attempt:
 * "NAME ok DETAIL" or "NAME refused DETAIL". Built as a program, shipped in the APK's native library folder and
 * run once by the isolated service and once by the app's own process, so the two reports can be compared. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <netinet/in.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/ptrace.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

static void said(const char *name, int worked, const char *detail) {
    printf("%s %s %s\n", name, worked ? "ok" : "refused", detail);
    fflush(stdout);
}

static void failed(const char *name) { said(name, 0, strerror(errno)); }

static void read_line(const char *name, const char *path) {
    char text[512] = {0};
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) { failed(name); return; }
    ssize_t n = read(fd, text, sizeof(text) - 1);
    close(fd);
    if (n < 0) { failed(name); return; }
    for (ssize_t i = 0; i < n; i++) if (text[i] == '\n' || text[i] == 0) text[i] = ' ';
    said(name, 1, text);
}

/* mov w0, #42; ret */
static const unsigned int answer_code[] = {0x52800540u, 0xd65f03c0u};

static void run_code(const char *name, void *at) {
    __builtin___clear_cache((char *)at, (char *)at + sizeof(answer_code));
    int got = ((int (*)(void))at)();
    said(name, got == 42, got == 42 ? "ran generated code" : "wrong answer");
}

static void memory(void) {
    void *both = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (both == MAP_FAILED) failed("jit.rwx");
    else { memcpy(both, answer_code, sizeof(answer_code)); run_code("jit.rwx", both); }
    void *later = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    memcpy(later, answer_code, sizeof(answer_code));
    if (mprotect(later, 4096, PROT_READ | PROT_EXEC) != 0) failed("jit.write_then_execute");
    else run_code("jit.write_then_execute", later);
    int memfd = (int)syscall(SYS_memfd_create, "probe", 0);
    if (memfd < 0) { failed("jit.memfd_map"); return; }
    if (write(memfd, answer_code, sizeof(answer_code)) != (ssize_t)sizeof(answer_code)) { failed("jit.memfd_map"); return; }
    void *shared = mmap(NULL, 4096, PROT_READ | PROT_EXEC, MAP_SHARED, memfd, 0);
    if (shared == MAP_FAILED) failed("jit.memfd_map");
    else run_code("jit.memfd_map", shared);
    close(memfd);
}

/* Runs `path` (or the file `fd` holds) in a child and says whether the kernel let it start. */
static void try_exec(const char *name, const char *path, int fd) {
    int told[2];
    if (pipe2(told, O_CLOEXEC) != 0) { failed(name); return; }
    pid_t child = fork();
    if (child < 0) { failed(name); return; }
    if (child == 0) {
        char *argv[] = {"probe", "child", NULL};
        char *envp[] = {NULL};
        int null = open("/dev/null", O_WRONLY);
        if (null >= 0) { dup2(null, 1); dup2(null, 2); }
        if (fd >= 0) syscall(SYS_execveat, fd, "", argv, envp, AT_EMPTY_PATH);
        else execve(path, argv, envp);
        int why = errno;
        if (write(told[1], &why, sizeof(why)) < 0) _exit(126);
        _exit(127);
    }
    close(told[1]);
    int why = 0;
    ssize_t n = read(told[0], &why, sizeof(why));
    close(told[0]);
    int status = 0;
    waitpid(child, &status, 0);
    if (n == (ssize_t)sizeof(why)) said(name, 0, strerror(why));
    else if (WIFEXITED(status) && WEXITSTATUS(status) == 0) said(name, 1, "started");
    else { char text[64]; snprintf(text, sizeof(text), "started, status %d", status); said(name, 1, text); }
}

static void copy_into(int out, const char *from) {
    int in = open(from, O_RDONLY | O_CLOEXEC);
    if (in < 0) return;
    char block[65536];
    ssize_t n;
    while ((n = read(in, block, sizeof(block))) > 0) if (write(out, block, (size_t)n) != n) break;
    close(in);
}

static void executing(const char *self, int app_file, const char *writable) {
    try_exec("exec.native_library_dir", self, -1);
    try_exec("exec.system_toybox", "/system/bin/true", -1);
    int memfd = (int)syscall(SYS_memfd_create, "exe", 0);
    copy_into(memfd, self);
    try_exec("exec.memfd", NULL, memfd);
    close(memfd);
    if (app_file >= 0) try_exec("exec.app_file_by_fd", NULL, app_file);
    if (writable != NULL) {
        char path[512];
        snprintf(path, sizeof(path), "%s/written-then-run", writable);
        int out = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0700);
        if (out < 0) failed("exec.write_a_file_then_run_it");
        else { copy_into(out, self); close(out); try_exec("exec.write_a_file_then_run_it", path, -1); }
    }
}

static int trapped = 0;
static void on_sys(int sig, siginfo_t *info, void *ctx) { (void)sig; (void)info; (void)ctx; trapped = 1; }

static void tracing(void) {
    pid_t child = fork();
    if (child == 0) {
        if (ptrace(PTRACE_TRACEME, 0, 0, 0) != 0) _exit(1);
        raise(SIGSTOP);
        _exit(0);
    }
    int status = 0;
    waitpid(child, &status, 0);
    if (WIFSTOPPED(status)) { said("trace.ptrace_own_child", 1, "child stopped under tracing"); ptrace(PTRACE_DETACH, child, 0, 0); }
    else said("trace.ptrace_own_child", 0, "child could not be traced");
    waitpid(child, &status, 0);
    child = fork();
    if (child == 0) {
        struct sigaction action;
        memset(&action, 0, sizeof(action));
        action.sa_sigaction = on_sys;
        action.sa_flags = SA_SIGINFO;
        sigaction(SIGSYS, &action, NULL);
        struct sock_filter code[] = {
            BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_getppid, 0, 1),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_TRAP),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        };
        struct sock_fprog program = {4, code};
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
        if (syscall(__NR_seccomp, SECCOMP_SET_MODE_FILTER, 0, &program) != 0) _exit(1);
        syscall(__NR_getppid);
        _exit(trapped ? 0 : 2);
    }
    waitpid(child, &status, 0);
    said("trace.seccomp_trap", WIFEXITED(status) && WEXITSTATUS(status) == 0, "own filter that traps a system call");
    child = fork();
    if (child == 0) {
        struct sock_filter code[] = {BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)};
        struct sock_fprog program = {1, code};
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
        long listener = syscall(__NR_seccomp, SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_NEW_LISTENER, &program);
        _exit(listener >= 0 ? 0 : 1);
    }
    waitpid(child, &status, 0);
    said("trace.seccomp_user_notify", WIFEXITED(status) && WEXITSTATUS(status) == 0, "listener for a filter");
}

static void socket_try(const char *name, int domain, int type, int protocol) {
    int fd = socket(domain, type | SOCK_CLOEXEC, protocol);
    if (fd < 0) failed(name);
    else { said(name, 1, "socket made"); close(fd); }
}

static void network(const char *app_socket, int app_port) {
    socket_try("net.tcp4", AF_INET, SOCK_STREAM, 0);
    socket_try("net.udp4", AF_INET, SOCK_DGRAM, 0);
    socket_try("net.tcp6", AF_INET6, SOCK_STREAM, 0);
    socket_try("net.netlink_route", AF_NETLINK, SOCK_RAW, 0);
    socket_try("net.vsock", 40 /* AF_VSOCK */, SOCK_STREAM, 0);
    socket_try("net.unix", AF_UNIX, SOCK_STREAM, 0);
    if (app_port > 0) {
        int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
        struct sockaddr_in to = {.sin_family = AF_INET, .sin_port = htons((uint16_t)app_port)};
        to.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (fd < 0 || connect(fd, (struct sockaddr *)&to, sizeof(to)) != 0) failed("net.connect_app_loopback");
        else said("net.connect_app_loopback", 1, "reached the app's loopback listener");
        if (fd >= 0) close(fd);
    }
    if (app_socket != NULL) {
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        struct sockaddr_un to = {.sun_family = AF_UNIX};
        size_t n = strlen(app_socket);
        memcpy(to.sun_path + 1, app_socket, n);
        socklen_t length = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + n);
        if (fd < 0 || connect(fd, (struct sockaddr *)&to, length) != 0) failed("net.connect_app_abstract_socket");
        else said("net.connect_app_abstract_socket", 1, "reached the app's abstract socket");
        if (fd >= 0) close(fd);
    }
}

static void opening(const char *name, const char *path, int flags) {
    int fd = open(path, flags | O_CLOEXEC);
    if (fd < 0) failed(name);
    else { said(name, 1, path); close(fd); }
}

static void listing(const char *name, const char *path) {
    DIR *folder = opendir(path);
    if (folder == NULL) { failed(name); return; }
    int entries = 0, processes = 0;
    struct dirent *entry;
    while ((entry = readdir(folder)) != NULL) {
        entries++;
        if (entry->d_name[0] >= '0' && entry->d_name[0] <= '9') processes++;
    }
    closedir(folder);
    char text[96];
    snprintf(text, sizeof(text), "%d entries, %d numbered", entries, processes);
    said(name, 1, text);
}

static void files(const char *secret, const char *folder, int app_pid, int app_file) {
    if (secret != NULL) opening("files.app_secret_by_path", secret, O_RDONLY);
    if (folder != NULL) listing("files.app_folder", folder);
    listing("files.proc", "/proc");
    listing("files.data_data", "/data/data");
    listing("files.sdcard", "/sdcard");
    if (app_pid > 0) {
        char path[96];
        const char *names[] = {"mem", "environ", "maps", "cmdline"};
        for (int i = 0; i < 4; i++) {
            char name[64];
            snprintf(path, sizeof(path), "/proc/%d/%s", app_pid, names[i]);
            snprintf(name, sizeof(name), "files.app_proc_%s", names[i]);
            opening(name, path, O_RDONLY);
        }
        if (kill(app_pid, 0) != 0) failed("process.signal_app");
        else said("process.signal_app", 1, "may signal the app");
        if (ptrace(PTRACE_ATTACH, app_pid, 0, 0) != 0) failed("process.ptrace_app");
        else { said("process.ptrace_app", 1, "attached to the app"); waitpid(app_pid, NULL, __WALL); ptrace(PTRACE_DETACH, app_pid, 0, 0); }
    }
    opening("device.binder", "/dev/binder", O_RDWR);
    opening("device.hwbinder", "/dev/hwbinder", O_RDWR);
    opening("device.kvm", "/dev/kvm", O_RDWR);
    opening("device.gunyah", "/dev/gunyah", O_RDWR);
    if (app_file >= 0) {
        char byte[64] = {0};
        ssize_t n = pread(app_file, byte, sizeof(byte) - 1, 0);
        if (n < 0) failed("handed.read");
        else said("handed.read", 1, "read the file the app handed over");
        void *map = mmap(NULL, 4096, PROT_READ, MAP_PRIVATE, app_file, 0);
        if (map == MAP_FAILED) failed("handed.map_read"); else { said("handed.map_read", 1, "mapped"); munmap(map, 4096); }
        map = mmap(NULL, 4096, PROT_READ | PROT_EXEC, MAP_PRIVATE, app_file, 0);
        if (map == MAP_FAILED) failed("handed.map_execute"); else { said("handed.map_execute", 1, "mapped for execution"); munmap(map, 4096); }
        char again[64];
        snprintf(again, sizeof(again), "/proc/self/fd/%d", app_file);
        opening("handed.reopen", again, O_RDONLY);
    }
}

/* For every process of another user id that /proc shows, which of its records this one may read. */
static void others(void) {
    static const char *const records[] = {"stat", "status", "cmdline", "environ", "maps", "fd"};
    enum { KINDS = 6 };
    int readable[KINDS] = {0}, seen = 0;
    DIR *proc = opendir("/proc");
    if (proc == NULL) { failed("process.others"); return; }
    struct dirent *entry;
    while ((entry = readdir(proc)) != NULL) {
        if (entry->d_name[0] < '0' || entry->d_name[0] > '9') continue;
        char path[320];
        struct stat info;
        snprintf(path, sizeof(path), "/proc/%s", entry->d_name);
        if (stat(path, &info) == 0 && info.st_uid == getuid()) continue;
        seen++;
        for (int i = 0; i < KINDS; i++) {
            snprintf(path, sizeof(path), "/proc/%s/%s", entry->d_name, records[i]);
            if (i == KINDS - 1) {
                DIR *folder = opendir(path);
                if (folder != NULL) { readable[i]++; closedir(folder); }
                continue;
            }
            char byte;
            int fd = open(path, O_RDONLY | O_CLOEXEC);
            if (fd < 0) continue;
            if (read(fd, &byte, 1) == 1) readable[i]++;
            close(fd);
        }
    }
    closedir(proc);
    char text[256];
    int used = snprintf(text, sizeof(text), "%d others listed; readable:", seen);
    int any = 0;
    for (int i = 0; i < KINDS; i++) {
        used += snprintf(text + used, sizeof(text) - (size_t)used, " %s %d", records[i], readable[i]);
        any += readable[i];
    }
    said("process.others", any > 0, text);
}

/* How many children survive a while: Android ends an app's extra child processes ("phantom processes"). */
static void children(int wanted, int seconds) {
    pid_t *made = calloc((size_t)wanted, sizeof(pid_t));
    int started = 0;
    for (int i = 0; i < wanted; i++) {
        pid_t child = fork();
        if (child == 0) { for (;;) pause(); }
        if (child > 0) made[started++] = child;
    }
    sleep((unsigned)seconds);
    int alive = 0;
    for (int i = 0; i < started; i++) {
        int status;
        if (waitpid(made[i], &status, WNOHANG) == 0) { alive++; kill(made[i], SIGKILL); waitpid(made[i], &status, 0); }
    }
    char text[96];
    snprintf(text, sizeof(text), "%d of %d started, %d alive after %d s", started, wanted, alive, seconds);
    said("process.children", alive == wanted, text);
    free(made);
}

static const char *value(int argc, char **argv, const char *name) {
    for (int i = 1; i + 1 < argc; i++) if (strcmp(argv[i], name) == 0) return argv[i + 1];
    return NULL;
}

static int number(int argc, char **argv, const char *name, int otherwise) {
    const char *found = value(argc, argv, name);
    return found == NULL ? otherwise : atoi(found);
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "child") == 0) return 0;
    char who[160];
    snprintf(who, sizeof(who), "uid %d gid %d pid %d", getuid(), getgid(), getpid());
    said("identity.uid", 1, who);
    read_line("identity.selinux", "/proc/self/attr/current");
    gid_t groups[32];
    int n = getgroups(32, groups);
    char list[256] = {0};
    for (int i = 0; i < n; i++) snprintf(list + strlen(list), sizeof(list) - strlen(list), "%d ", groups[i]);
    said("identity.groups", n >= 0, list);
    memory();
    int app_file = number(argc, argv, "--fd", -1);
    executing(argv[0], app_file, value(argc, argv, "--writable"));
    tracing();
    network(value(argc, argv, "--app-socket"), number(argc, argv, "--app-port", 0));
    files(value(argc, argv, "--secret"), value(argc, argv, "--app-folder"), number(argc, argv, "--app-pid", 0), app_file);
    others();
    int wanted = number(argc, argv, "--children", 0);
    if (wanted > 0) children(wanted, number(argc, argv, "--children-seconds", 20));
    puts("probe.end ok done");
    return 0;
}
