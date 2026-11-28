package app.poolhouse.worker;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * The Linux userland this app runs code in: a Debian root file system under proot, all inside the app's private
 * folder. proot is not a security boundary; the Android app sandbox is. Nothing outside the app's own folder, /dev,
 * /proc and /sys (as this app may read them) is mounted, and the app holds no storage permission.
 */
final class Guest {
    static final String PATH = "/opt/venv/bin:/opt/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
    /** Files Android keeps from apps that Linux programs read: bound over from a stand-in only when unreadable. */
    private static final String[][] STAND_INS = {
        {"/proc/stat", "cpu  0 0 0 0 0 0 0 0 0 0\nbtime 0\n"},
        {"/proc/loadavg", "0.00 0.00 0.00 1/1 1\n"},
        {"/proc/uptime", "1.00 1.00\n"},
        {"/proc/version", "Linux version (Android kernel)\n"},
        {"/proc/vmstat", "nr_free_pages 0\n"},
    };

    private final Context context;
    final File files;
    final File rootfs;
    final File work;
    final File logs;

    Guest(Context context) {
        this.context = context;
        files = context.getFilesDir();
        rootfs = new File(files, "rootfs");
        work = new File(files, "work");
        logs = new File(files, "logs");
    }

    /** Write the programs and guest scripts this build carries over whatever an earlier build left. */
    void unpack() throws IOException {
        for (File folder : new File[] {rootfs, work, new File(work, "home"), logs, new File(files, "shm"), tmp()}) {
            if (!folder.isDirectory() && !folder.mkdirs()) {
                throw new IOException("cannot make " + folder);
            }
        }
        for (String folder : new String[] {"bin", "lib", "guest"}) {
            File out = new File(files, folder);
            if (!out.isDirectory() && !out.mkdirs()) {
                throw new IOException("cannot make " + out);
            }
            String[] names = context.getAssets().list(folder);
            if (names == null) {
                continue;
            }
            for (String name : names) {
                File target = new File(out, name);
                File partial = new File(out, name + ".new");
                try (InputStream in = context.getAssets().open(folder + "/" + name)) {
                    Files.copy(in, partial.toPath(), StandardCopyOption.REPLACE_EXISTING);
                }
                if (!partial.setExecutable(true, true) || !partial.renameTo(target)) {
                    throw new IOException("cannot place " + target);
                }
            }
        }
    }

    private File tmp() {
        return new File(context.getCacheDir(), "proot");
    }

    /** The name servers of the phone's current network: Debian has no other way to learn them. */
    void writeResolver() throws IOException {
        StringBuilder text = new StringBuilder();
        ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
        Network network = manager == null ? null : manager.getActiveNetwork();
        LinkProperties links = network == null ? null : manager.getLinkProperties(network);
        if (links != null) {
            for (InetAddress server : links.getDnsServers()) {
                text.append("nameserver ").append(server.getHostAddress()).append('\n');
            }
        }
        write(new File(rootfs, "etc/resolv.conf"), text.toString());
        write(new File(rootfs, "etc/hosts"), "127.0.0.1 localhost\n::1 localhost ip6-localhost\n");
    }

    static void write(File file, String text) throws IOException {
        File parent = file.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            throw new IOException("cannot make " + parent);
        }
        Files.deleteIfExists(file.toPath());
        try (OutputStream out = Files.newOutputStream(file.toPath())) {
            out.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    private ProcessBuilder proot(List<String> arguments) {
        List<String> command = new ArrayList<>(Arrays.asList(new File(files, "bin/proot").getPath(), "--kill-on-exit", "--link2symlink"));
        command.addAll(arguments);
        ProcessBuilder builder = new ProcessBuilder(command).directory(files).redirectErrorStream(true);
        builder.environment().put("PROOT_LOADER", new File(files, "bin/loader").getPath());
        builder.environment().put("PROOT_TMP_DIR", tmp().getPath());
        builder.environment().put("LD_LIBRARY_PATH", new File(files, "lib").getPath());
        return builder;
    }

    /** Unpack the downloaded layer into the root file system (Android lets an app make no hard link). */
    ProcessBuilder extract(File archive) {
        return proot(Arrays.asList("/system/bin/tar", "-xzf", archive.getPath(), "-C", rootfs.getPath()));
    }

    /**
     * ``command`` inside the guest. ``packages`` makes it believe it is root, which the package manager needs; the
     * node and every test run without it, as this app's own unprivileged user.
     */
    ProcessBuilder run(boolean packages, String... command) throws IOException {
        List<String> arguments = new ArrayList<>();
        if (packages) {
            arguments.add("-0");
        }
        arguments.addAll(Arrays.asList("-r", rootfs.getPath(), "-b", "/dev", "-b", "/proc", "-b", "/sys",
                "-b", "/dev/urandom:/dev/random", "-b", new File(files, "shm").getPath() + ":/dev/shm",
                "-b", work.getPath() + ":/work", "-b", new File(files, "guest").getPath() + ":/opt/worker", "-w", "/work"));
        for (String[] stand : STAND_INS) {
            if (!readable(new File(stand[0]))) {
                File held = new File(files, "proc" + stand[0].substring("/proc".length()));
                write(held, stand[1]);
                arguments.add("-b");
                arguments.add(held.getPath() + ":" + stand[0]);
            }
        }
        arguments.addAll(Arrays.asList("/usr/bin/env", "-i", "HOME=/work/home", "PATH=" + PATH, "LANG=C.UTF-8",
                "TMPDIR=/tmp", "APP_UID=" + android.os.Process.myUid()));
        arguments.addAll(Arrays.asList(command));
        return proot(arguments);
    }

    private static boolean readable(File file) {
        try (InputStream in = Files.newInputStream(file.toPath())) {
            return in.read() >= 0;
        } catch (IOException refused) {
            return false;
        }
    }

    /** End every process of this app's user except the app itself: the guest and everything it started. */
    static void endProcesses() {
        int own = android.os.Process.myPid();
        String uid = "Uid:\t" + android.os.Process.myUid() + "\t";
        File[] entries = new File("/proc").listFiles();
        if (entries == null) {
            return;
        }
        for (File entry : entries) {
            if (!entry.getName().matches("[0-9]+") || Integer.parseInt(entry.getName()) == own) {
                continue;
            }
            try {
                for (String line : Files.readAllLines(new File(entry, "status").toPath())) {
                    if (line.startsWith(uid)) {
                        android.os.Process.killProcess(Integer.parseInt(entry.getName()));
                    }
                }
            } catch (IOException gone) {
                // The process ended while it was being read.
            }
        }
    }
}
