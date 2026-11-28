package app.poolhouse.isolation;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.net.LocalServerSocket;
import android.os.Bundle;
import android.os.IBinder;
import android.os.Message;
import android.os.Messenger;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.os.RemoteException;
import android.os.SystemClock;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Log;
import android.view.WindowManager;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyStore;

import javax.crypto.KeyGenerator;

/** The app's side of the prototype. It holds what a node would hold (a secret file, a keystore key, a listener),
 * opens the descriptors the isolated service gets, and writes both reports to its own folder and the log. */
public final class ProbeActivity extends Activity {
    static final String ALIAS = "app-secret";
    private static final String TAG = "ISOLATION";

    private ServerSocket listener;
    private LocalServerSocket local;
    private ServiceConnection connection;
    /** The app's end of the machine's channel, held so that it is not closed when nothing else refers to it. */
    private ParcelFileDescriptor channelEnd;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        TextView view = new TextView(this);
        view.setText("isolation prototype");
        setContentView(view);
        start(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        start(intent);
    }

    private void start(Intent intent) {
        String task = intent.getStringExtra("task");
        if (task == null) {
            return;
        }
        try {
            if (task.equals("probe")) {
                probe(intent);
            } else if (task.equals("machine")) {
                machine(intent);
            } else if (task.equals("stop") && connection != null) {
                // Letting go is the app's only way to end the runner: it may not signal another user id.
                unbindService(connection);
                connection = null;
            }
        } catch (Exception failure) {
            Log.e(TAG, task + " failed", failure);
        }
    }

    private interface Connected {
        void with(Messenger runner) throws Exception;
    }

    private void withRunner(Connected body) {
        if (connection != null) {
            unbindService(connection);
        }
        connection = new ServiceConnection() {
            @Override
            public void onServiceConnected(ComponentName name, IBinder service) {
                try {
                    body.with(new Messenger(service));
                } catch (Exception failure) {
                    Log.e(TAG, "the runner could not be asked", failure);
                }
            }

            @Override
            public void onServiceDisconnected(ComponentName name) {
                Log.w(TAG, "the runner ended");
            }
        };
        bindService(new Intent(this, IsolatedRunner.class), connection,
                Context.BIND_AUTO_CREATE | Context.BIND_IMPORTANT);
    }

    /** Copies lines of `from` to the log and to the file `name`, each stamped with the milliseconds since `from`
     * was opened, until it closes. */
    private void record(String name, InputStream from) {
        long began = SystemClock.elapsedRealtime();
        new Thread(() -> {
            try (BufferedReader lines = new BufferedReader(new InputStreamReader(from, StandardCharsets.UTF_8));
                 OutputStream to = new FileOutputStream(new File(getFilesDir(), name))) {
                String line;
                while ((line = lines.readLine()) != null) {
                    String stamped = (SystemClock.elapsedRealtime() - began) + " " + line;
                    to.write((stamped + "\n").getBytes(StandardCharsets.UTF_8));
                    to.flush();
                    Log.i(TAG, name + " " + stamped);
                }
            } catch (IOException failure) {
                Log.e(TAG, name + " stopped", failure);
            }
            Log.i(TAG, name + " closed");
        }, name).start();
    }

    private void probe(Intent intent) throws Exception {
        File secret = new File(getFilesDir(), "secret.txt");
        Files.write(secret.toPath(), "a credential only the app may read\n".getBytes(StandardCharsets.UTF_8));
        getSharedPreferences("secrets", MODE_PRIVATE).edit().putString("token", "a token").commit();
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT).build());
            generator.generateKey();
        }
        if (listener == null) {
            listener = new ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"));
            local = new LocalServerSocket(getPackageName() + ".app");
        }
        File handed = new File(getFilesDir(), "handed.txt");
        Files.write(handed.toPath(), "handed to the runner\n".getBytes(StandardCharsets.UTF_8));
        String children = String.valueOf(intent.getIntExtra("children", 0));
        String seconds = String.valueOf(intent.getIntExtra("seconds", 20));

        // The same program as the app's own user, for comparison.
        ParcelFileDescriptor[] own = ParcelFileDescriptor.createPipe();
        record("probe-app.txt", new ParcelFileDescriptor.AutoCloseInputStream(own[0]));
        try (ParcelFileDescriptor out = own[1];
             ParcelFileDescriptor file = ParcelFileDescriptor.open(handed, ParcelFileDescriptor.MODE_READ_WRITE)) {
            Spawn.start(new String[]{getApplicationInfo().nativeLibraryDir + "/libprobe.so", "--fd", "3",
                    "--secret", secret.getPath(), "--app-folder", getFilesDir().getPath(),
                    "--app-port", String.valueOf(listener.getLocalPort()),
                    "--app-socket", getPackageName() + ".app", "--writable", getFilesDir().getPath()},
                    new String[0], new int[]{out.getFd(), file.getFd()});
        }

        withRunner(runner -> {
            ParcelFileDescriptor[] pipe = ParcelFileDescriptor.createPipe();
            record("probe-isolated.txt", new ParcelFileDescriptor.AutoCloseInputStream(pipe[0]));
            try (ParcelFileDescriptor out = pipe[1];
                 ParcelFileDescriptor file = ParcelFileDescriptor.open(handed,
                         ParcelFileDescriptor.MODE_READ_WRITE)) {
                Bundle asked = new Bundle();
                asked.putParcelable("out", out);
                asked.putParcelable("handed", file);
                asked.putString("secret", secret.getPath());
                asked.putString("app-folder", getFilesDir().getPath());
                asked.putString("app-pid", String.valueOf(Process.myPid()));
                asked.putString("app-socket", getPackageName() + ".app");
                asked.putString("app-port", String.valueOf(listener.getLocalPort()));
                asked.putInt("app_port", listener.getLocalPort());
                asked.putString("children", children);
                asked.putString("children-seconds", seconds);
                send(runner, IsolatedRunner.PROBE, asked);
            }
        });
    }

    private static void send(Messenger to, int what, Bundle data) throws RemoteException {
        Message message = Message.obtain(null, what);
        message.setData(data);
        to.send(message);
    }

    /** Starts the machine on `files/vm/system.img`, with a work disk made here, and sends the guest the script
     * `files/vm/task.sh`. Its console goes to console.txt, its answer to result.txt. */
    private void machine(Intent intent) {
        File folder = new File(getFilesDir(), "vm");
        File system = new File(folder, "system.img");
        File work = new File(folder, "work.img");
        File task = new File(folder, "task.sh");
        withRunner(runner -> {
            try (RandomAccessFile made = new RandomAccessFile(work, "rw")) {
                if (made.length() == 0) {
                    made.setLength(intent.getIntExtra("work", 4096) * 1048576L);
                }
            }
            ParcelFileDescriptor[] console = ParcelFileDescriptor.createPipe();
            record("console.txt", new ParcelFileDescriptor.AutoCloseInputStream(console[0]));
            ParcelFileDescriptor[] channel = ParcelFileDescriptor.createSocketPair();
            channelEnd = channel[0];
            record("result.txt", new FileInputStream(channelEnd.getFileDescriptor()));
            byte[] script = Files.readAllBytes(task.toPath());
            new Thread(() -> {
                try {
                    OutputStream to = new FileOutputStream(channelEnd.getFileDescriptor());
                    to.write((script.length + "\n").getBytes(StandardCharsets.UTF_8));
                    to.write(script);
                    to.flush();
                    Log.i(TAG, "task sent " + script.length + " bytes");
                } catch (IOException failure) {
                    Log.e(TAG, "the task could not be sent", failure);
                }
            }, "task").start();
            try (ParcelFileDescriptor out = console[1];
                 ParcelFileDescriptor guest = channel[1];
                 ParcelFileDescriptor base = ParcelFileDescriptor.open(system, ParcelFileDescriptor.MODE_READ_ONLY);
                 ParcelFileDescriptor disk = ParcelFileDescriptor.open(work, ParcelFileDescriptor.MODE_READ_WRITE)) {
                Bundle asked = new Bundle();
                asked.putParcelable("out", out);
                asked.putParcelable("system", base);
                asked.putParcelable("work", disk);
                asked.putParcelable("channel", guest);
                asked.putInt("cpus", intent.getIntExtra("cpus", 4));
                asked.putInt("memory", intent.getIntExtra("memory", 2048));
                asked.putInt("translations", intent.getIntExtra("translations", 512));
                for (String name : new String[]{"cpu", "kernel"}) {
                    String value = intent.getStringExtra(name);
                    if (value != null) {
                        asked.putString(name, value);
                    }
                }
                send(runner, IsolatedRunner.VM, asked);
            }
        });
    }
}
