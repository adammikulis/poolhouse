package app.poolhouse.isolation;

import android.app.Service;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Message;
import android.os.Messenger;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.io.FileOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.List;

import javax.crypto.KeyGenerator;

/** The isolated service: everything untrusted runs below this process. It gets descriptors from the app and
 * nothing else: it can open none of the app's files, use none of its keys and make no network socket. */
public final class IsolatedRunner extends Service {
    static final int PROBE = 1;
    static final int VM = 2;

    private Messenger messenger;

    @Override
    public IBinder onBind(Intent intent) {
        HandlerThread thread = new HandlerThread("runner");
        thread.start();
        messenger = new Messenger(new Handler(thread.getLooper(), this::handle));
        return messenger.getBinder();
    }

    private boolean handle(Message message) {
        Bundle asked = message.getData();
        asked.setClassLoader(ParcelFileDescriptor.class.getClassLoader());
        if (message.what == PROBE) {
            probe(asked);
        } else if (message.what == VM) {
            new Thread(() -> machine(asked), "machine").start();
        }
        return true;
    }

    private static ParcelFileDescriptor descriptor(Bundle asked, String name) {
        return asked.getParcelable(name, ParcelFileDescriptor.class);
    }

    private interface Attempt {
        Object run() throws Exception;
    }

    private static void attempt(PrintStream out, String name, Attempt body) {
        try {
            out.println(name + " ok " + body.run());
        } catch (Throwable failure) {
            out.println(name + " refused " + failure.getClass().getSimpleName() + ": " + failure.getMessage());
        }
        out.flush();
    }

    /** What the Java side of this process can reach, then what native code in a child of it can. */
    private void probe(Bundle asked) {
        try (ParcelFileDescriptor out = descriptor(asked, "out");
             ParcelFileDescriptor handed = descriptor(asked, "handed")) {
            PrintStream report = new PrintStream(new FileOutputStream(out.getFileDescriptor()), true);
            report.println("java.identity ok uid " + Process.myUid() + " isolated " + Process.isIsolated());
            attempt(report, "java.keystore_make_key", () -> {
                KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
                generator.init(new KeyGenParameterSpec.Builder("isolated",
                        KeyProperties.PURPOSE_ENCRYPT).build());
                generator.generateKey();
                return "made a key";
            });
            attempt(report, "java.keystore_app_key", () -> {
                KeyStore store = KeyStore.getInstance("AndroidKeyStore");
                store.load(null);
                if (!store.containsAlias(ProbeActivity.ALIAS)) {
                    throw new SecurityException("the app's key is not visible");
                }
                return "sees the app's key";
            });
            attempt(report, "java.app_preferences", () -> getSharedPreferences("secrets", MODE_PRIVATE)
                    .getString("token", "nothing"));
            attempt(report, "java.app_files", () -> String.valueOf(openFileInput("secret.txt").read()));
            attempt(report, "java.loopback_socket", () -> {
                try (Socket socket = new Socket()) {
                    socket.connect(new InetSocketAddress("127.0.0.1", asked.getInt("app_port")), 2000);
                    return "connected";
                }
            });
            attempt(report, "java.content_provider", () -> getContentResolver().query(
                    android.provider.Settings.System.CONTENT_URI, null, null, null, null));
            attempt(report, "java.start_activity", () -> {
                startActivity(new Intent(Intent.ACTION_VIEW).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        .setClassName(getPackageName(), ProbeActivity.class.getName()));
                return "asked without an error";
            });
            attempt(report, "java.send_broadcast", () -> {
                sendBroadcast(new Intent("app.poolhouse.isolation.FROM_RUNNER").setPackage(getPackageName()));
                return "asked without an error";
            });
            attempt(report, "java.start_service", () -> String.valueOf(
                    startService(new Intent(this, IsolatedRunner.class))));
            attempt(report, "java.clipboard", () -> String.valueOf(getSystemService(CLIPBOARD_SERVICE)));
            attempt(report, "java.connectivity", () -> String.valueOf(getSystemService(CONNECTIVITY_SERVICE)));
            List<String> argv = new ArrayList<>(List.of(getApplicationInfo().nativeLibraryDir + "/libprobe.so",
                    "--fd", "3"));
            for (String name : new String[]{"secret", "app-folder", "app-pid", "app-socket", "app-port", "children",
                    "children-seconds"}) {
                String found = asked.getString(name);
                if (found != null) {
                    argv.add("--" + name);
                    argv.add(found);
                }
            }
            int child = Spawn.start(argv.toArray(new String[0]), new String[0],
                    new int[]{out.getFd(), handed.getFd()});
            report.println("native.exit " + (child > 0 ? "ok " + Spawn.await(child) : "refused " + child));
        } catch (IOException failure) {
            throw new IllegalStateException(failure);
        }
    }

    /** Runs the virtual machine: the engine and its guest kernel come from the APK, every disk and the one channel
     * to the app are descriptors the app opened, and the guest has no network device at all. */
    private void machine(Bundle asked) {
        String library = getApplicationInfo().nativeLibraryDir;
        try (ParcelFileDescriptor out = descriptor(asked, "out");
             ParcelFileDescriptor system = descriptor(asked, "system");
             ParcelFileDescriptor work = descriptor(asked, "work");
             ParcelFileDescriptor channel = descriptor(asked, "channel")) {
            List<String> argv = new ArrayList<>(List.of(library + "/libqemu.so",
                    "-nodefaults", "-no-user-config", "-nographic", "-no-reboot",
                    "-machine", "virt", "-cpu", asked.getString("cpu", "max,pauth-impdef=on"),
                    "-accel", "tcg,thread=multi,tb-size=" + asked.getInt("translations", 512),
                    "-smp", String.valueOf(asked.getInt("cpus", 4)),
                    "-m", String.valueOf(asked.getInt("memory", 2048)),
                    "-kernel", library + "/libkernel.so", "-initrd", library + "/libinitrd.so",
                    "-append", "console=ttyAMA0 quiet panic=1 " + asked.getString("kernel", ""),
                    "-serial", "stdio",
                    "-add-fd", "fd=3,set=1", "-add-fd", "fd=4,set=2",
                    "-blockdev", "driver=file,node-name=systemfile,filename=/dev/fdset/1,read-only=on,locking=off",
                    "-blockdev", "driver=raw,node-name=system,file=systemfile,read-only=on",
                    "-device", "virtio-blk-device,drive=system,serial=system",
                    "-blockdev", "driver=file,node-name=workfile,filename=/dev/fdset/2,locking=off,discard=unmap",
                    "-blockdev", "driver=raw,node-name=work,file=workfile,discard=unmap",
                    "-device", "virtio-blk-device,drive=work,serial=work",
                    "-chardev", "socket,id=app,fd=5",
                    "-device", "virtio-serial-device",
                    "-device", "virtserialport,chardev=app,name=app.poolhouse.channel"));
            String[] environment = {"LD_LIBRARY_PATH=" + library, "TMPDIR=/nonexistent"};
            int child = Spawn.start(argv.toArray(new String[0]), environment,
                    new int[]{out.getFd(), system.getFd(), work.getFd(), channel.getFd()});
            PrintStream report = new PrintStream(new FileOutputStream(out.getFileDescriptor()), true);
            if (child <= 0) {
                report.println("machine.start refused " + child);
                return;
            }
            report.println("machine.exit " + Spawn.await(child));
        } catch (IOException failure) {
            throw new IllegalStateException(failure);
        }
    }
}
