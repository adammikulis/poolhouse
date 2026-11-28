package app.poolhouse.worker;

import android.content.Intent;

import java.io.File;
import java.io.IOException;

/**
 * The debug build's way in for a computer the owner allowed to debug the phone: only the shell holds the permission that
 * guards it, and the release manifest does not name it. ``JOB`` runs ``work/jobs/NAME.sh`` in the guest, as the
 * app, in the app's own scheduling group, so what is measured is what a pool run would get.
 */
public class DebugWorkerService extends WorkerService {
    public static final String JOB = "app.poolhouse.worker.JOB";

    @Override
    boolean other(Intent intent) {
        String name = intent.getStringExtra("name");
        if (!JOB.equals(intent.getAction()) || name == null || !name.matches("[a-z0-9-]{1,40}")) {
            return false;
        }
        stayAwake();
        Guest guest = new Guest(this);
        new Thread(() -> job(guest, name), "pool-job").start();
        return true;
    }

    private void job(Guest guest, String name) {
        File status = new File(guest.logs, "job-" + name + ".status");
        try {
            guest.unpack();
            Guest.write(status, "running\n");
            Process process = guest.run(false, "/bin/sh", "/work/jobs/" + name + ".sh")
                    .redirectOutput(new File(guest.logs, "job-" + name + ".log")).start();
            Guest.write(status, "ended " + process.waitFor() + "\n");
        } catch (IOException failed) {
            android.util.Log.e("poolhouse-worker", "job " + name, failed);
        } catch (InterruptedException stopped) {
            Thread.currentThread().interrupt();
        }
    }
}
