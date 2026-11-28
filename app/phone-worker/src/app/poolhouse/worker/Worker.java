package app.poolhouse.worker;

import java.io.File;
import java.io.IOException;

/**
 * The one thread that does this phone's work: it sets the guest up, then keeps the node running until it is
 * stopped. A node that ends is started again after a pause that doubles up to five minutes.
 */
final class Worker extends Thread {
    interface Listener {
        void state(String words);
    }

    private static final long LONGEST_PAUSE_MS = 300_000;

    private final Guest guest;
    private final Listener listener;
    private volatile boolean stopping;

    Worker(Guest guest, Listener listener) {
        super("pool-worker");
        this.guest = guest;
        this.listener = listener;
    }

    void say(String words) throws IOException {
        Guest.write(new File(guest.files, "status"), words + "\n");
        listener.state(words);
    }

    /** Run ``command`` to its end with its output in ``logs/NAME.log``; an error when it fails. */
    void finish(ProcessBuilder command, String name) throws IOException, InterruptedException {
        File log = new File(guest.logs, name + ".log");
        Process process = command.redirectOutput(ProcessBuilder.Redirect.appendTo(log)).start();
        int status = process.waitFor();
        if (status != 0) {
            throw new IOException(name + " ended with status " + status + "; see logs/" + name + ".log");
        }
    }

    void end() {
        stopping = true;
        interrupt();
        Guest.endProcesses();
    }

    @Override
    public void run() {
        long pause = 5_000;
        while (!stopping) {
            try {
                new Setup(guest).ensure(this);
                say("working as a pool device");
                finish(guest.run(false, "/bin/sh", "/opt/worker/node.sh"), "node");
                pause = 5_000;
            } catch (IOException failed) {
                try {
                    say("not working: " + failed.getMessage());
                } catch (IOException unwritable) {
                    listener.state("not working: " + unwritable.getMessage());
                }
            } catch (InterruptedException stopped) {
                return;
            }
            try {
                Thread.sleep(pause);
            } catch (InterruptedException stopped) {
                return;
            }
            pause = Math.min(pause * 2, LONGEST_PAUSE_MS);
        }
    }
}
