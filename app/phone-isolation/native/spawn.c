/* Starts a program from the APK's native library folder with chosen descriptors. Java's ProcessBuilder hands a
 * child no descriptor beyond the three standard ones, and the isolated process can open none of the app's files
 * itself: the app opens each one and it reaches the child here as 3, 4, 5, in the order given. */
#include <errno.h>
#include <fcntl.h>
#include <jni.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

static char **words(JNIEnv *env, jobjectArray from) {
    jsize n = (*env)->GetArrayLength(env, from);
    char **made = calloc((size_t)n + 1, sizeof(char *));
    for (jsize i = 0; i < n; i++) {
        jstring one = (jstring)(*env)->GetObjectArrayElement(env, from, i);
        const char *text = (*env)->GetStringUTFChars(env, one, NULL);
        made[i] = strdup(text);
        (*env)->ReleaseStringUTFChars(env, one, text);
    }
    return made;
}

static void release(char **made) {
    for (int i = 0; made[i] != NULL; i++) free(made[i]);
    free(made);
}

/* `descriptors[0]` becomes the child's standard output and error; the rest become 3, 4, and so on. */
JNIEXPORT jint JNICALL Java_app_poolhouse_isolation_Spawn_start(JNIEnv *env, jclass type, jobjectArray argv,
                                                                jobjectArray envp, jintArray descriptors) {
    (void)type;
    jsize count = (*env)->GetArrayLength(env, descriptors);
    if (count < 1 || count > 16) return -EINVAL;
    jint given[16];
    (*env)->GetIntArrayRegion(env, descriptors, 0, count, given);
    char **args = words(env, argv);
    char **environment = words(env, envp);
    pid_t child = fork();
    if (child == 0) {
        int moved[16];
        for (int i = 0; i < count; i++) if ((moved[i] = fcntl(given[i], F_DUPFD, 100)) < 0) _exit(125);
        int null = open("/dev/null", O_RDONLY);
        if (null >= 0) dup2(null, 0);
        dup2(moved[0], 1);
        dup2(moved[0], 2);
        for (int i = 1; i < count; i++) dup2(moved[i], 2 + i);
        for (int fd = 2 + count; fd < 1024; fd++) close(fd);
        prctl(PR_SET_PDEATHSIG, SIGKILL);
        execve(args[0], args, environment);
        _exit(127);
    }
    int why = errno;
    release(args);
    release(environment);
    return child < 0 ? -why : child;
}

JNIEXPORT jint JNICALL Java_app_poolhouse_isolation_Spawn_await(JNIEnv *env, jclass type, jint child) {
    (void)env;
    (void)type;
    int status = 0;
    while (waitpid(child, &status, 0) < 0) if (errno != EINTR) return -errno;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
