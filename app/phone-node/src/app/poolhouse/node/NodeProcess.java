package app.poolhouse.node;

import android.content.Context;
import android.util.Log;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Keeps the node's process running. The program is the node built for Android and installed with the app (the one
 * place a current app may execute from); its state is the app's private {@code files/node}. It listens on a network
 * only when {@code files/node-listen} names an address: with no such file it is a device in no pool that nothing
 * reaches. A node that ends is started again after a pause that doubles up to five minutes.
 */
final class NodeProcess extends Thread {
    private static final long LONGEST_PAUSE_MS = 300_000;

    private final Context context;
    private volatile boolean stopping;
    private volatile Process process;

    NodeProcess(Context context) {
        super("pool-node");
        this.context = context;
    }

    private List<String> command() throws IOException {
        File program = new File(context.getApplicationInfo().nativeLibraryDir, "libpoolhouse_node.so");
        List<String> command = new ArrayList<>(Arrays.asList(program.getPath(), "run", "--state",
                new File(context.getFilesDir(), "node").getPath(), "--presence", "auto"));
        File listen = new File(context.getFilesDir(), "node-listen");
        if (listen.isFile()) {
            command.add("--listen");
            command.add(new String(Files.readAllBytes(listen.toPath()), StandardCharsets.UTF_8).trim());
        }
        return command;
    }

    void end() {
        stopping = true;
        interrupt();
        Process running = process;
        if (running != null) {
            running.destroy();
        }
    }

    @Override
    public void run() {
        long pause = 1_000;
        File log = new File(context.getFilesDir(), "node.log");
        while (!stopping) {
            try {
                ProcessBuilder builder = new ProcessBuilder(command()).redirectErrorStream(true)
                        .redirectOutput(ProcessBuilder.Redirect.appendTo(log));
                builder.environment().put("HOME", context.getFilesDir().getPath());
                process = builder.start();
                if (stopping) {
                    process.destroy();
                }
                Log.w("poolhouse-node", "the node ended with status " + process.waitFor());
            } catch (IOException failed) {
                Log.e("poolhouse-node", "the node did not start", failed);
            } catch (InterruptedException ended) {
                return;
            }
            try {
                Thread.sleep(pause);
            } catch (InterruptedException ended) {
                return;
            }
            pause = Math.min(pause * 2, LONGEST_PAUSE_MS);
        }
    }
}
