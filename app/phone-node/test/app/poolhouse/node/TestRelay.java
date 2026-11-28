package app.poolhouse.node;

import android.app.Service;
import android.content.Intent;
import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import android.os.IBinder;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

/**
 * In the test app alone, for an emulator: the node's local socket on the loopback port {@code 47812}, so a computer
 * can drive it through {@code adb forward}. The calls reach the node from the app's own process, as the app's will.
 * Any program on that device could use the port, so it is in no build a person installs.
 */
public class TestRelay extends Service {
    private static boolean listening;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        synchronized (TestRelay.class) {
            if (!listening) {
                listening = true;
                new Thread(this::serve, "test-relay").start();
            }
        }
        return START_NOT_STICKY;
    }

    private void serve() {
        String node = new File(getFilesDir(), "node/node.sock").getPath();
        try (ServerSocket server = new ServerSocket(47812, 16, InetAddress.getLoopbackAddress())) {
            while (true) {
                connect(server.accept(), node);
            }
        } catch (IOException failed) {
            Log.e("poolhouse-node", "the test relay ended", failed);
        }
    }

    private static void connect(Socket from, String node) {
        LocalSocket to = new LocalSocket();
        try {
            to.connect(new LocalSocketAddress(node, LocalSocketAddress.Namespace.FILESYSTEM));
            pump(from.getInputStream(), to.getOutputStream(), () -> to.shutdownOutput());
            pump(to.getInputStream(), from.getOutputStream(), from::close);
        } catch (IOException failed) {
            Log.w("poolhouse-node", "the test relay could not connect", failed);
        }
    }

    private interface End {
        void run() throws IOException;
    }

    private static void pump(InputStream in, OutputStream out, End end) {
        new Thread(() -> {
            byte[] block = new byte[1 << 16];
            try {
                for (int read = in.read(block); read >= 0; read = in.read(block)) {
                    out.write(block, 0, read);
                    out.flush();
                }
                end.run();
            } catch (IOException closed) {
                Log.d("poolhouse-node", "a relayed connection closed", closed);
            }
        }, "test-relay-pump").start();
    }
}
