package app.poolhouse.isolation;

/** Starts a program of the APK's native library folder with chosen descriptors (native/spawn.c). */
final class Spawn {
    static {
        System.loadLibrary("spawn");
    }

    private Spawn() {
    }

    /** The child's process id, or a negative error number. descriptors[0] is its output; the rest are 3, 4, ... */
    static native int start(String[] argv, String[] environment, int[] descriptors);

    /** The child's exit status, 128 plus the signal that ended it, or a negative error number. */
    static native int await(int child);
}
