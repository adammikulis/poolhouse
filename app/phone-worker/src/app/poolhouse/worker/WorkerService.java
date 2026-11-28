package app.poolhouse.worker;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * The phone as a pool device, off until it is switched on. The Poolhouse app holds the one switch and reaches this
 * service with a permission only an app signed with the same key can hold; this app shows no switch of its own, and
 * its notification can only turn the work off.
 */
public class WorkerService extends Service {
    public static final String ENABLE = "app.poolhouse.worker.ENABLE";
    public static final String DISABLE = "app.poolhouse.worker.DISABLE";
    private static final String CHANNEL = "worker";

    // One worker and one wake lock for the process, whichever of its services an intent reaches.
    private static Worker worker;
    private static PowerManager.WakeLock awake;

    private SharedPreferences preferences() {
        return getSharedPreferences("worker", MODE_PRIVATE);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        startForeground(1, notification("starting"));
        String action = intent == null ? null : intent.getAction();
        if (ENABLE.equals(action)) {
            preferences().edit().putBoolean("enabled", true).apply();
        } else if (DISABLE.equals(action)) {
            preferences().edit().putBoolean("enabled", false).apply();
            halt();
        }
        if (action != null && other(intent)) {
            return START_NOT_STICKY;
        }
        if (preferences().getBoolean("enabled", false)) {
            start();
            return START_STICKY;
        }
        halt();
        stopForeground(true);
        stopSelf();
        return START_NOT_STICKY;
    }

    /** An action a subclass adds, true when it took the intent and keeps the service; this service has none. */
    boolean other(Intent intent) {
        return false;
    }

    /** The node answers other devices and runs tests with the screen off, so the processor stays awake while it works. */
    void stayAwake() {
        synchronized (WorkerService.class) {
            if (awake == null) {
                awake = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "poolhouse:worker");
                awake.setReferenceCounted(false);
            }
            awake.acquire();
        }
    }

    private void start() {
        synchronized (WorkerService.class) {
            if (worker != null && worker.isAlive()) {
                return;
            }
            stayAwake();
            Context app = getApplicationContext();
            worker = new Worker(new Guest(app), words -> app.getSystemService(NotificationManager.class).notify(1, notification(words)));
            worker.start();
        }
    }

    private static void halt() {
        synchronized (WorkerService.class) {
            if (worker != null) {
                worker.end();
                worker = null;
            } else {
                Guest.endProcesses();
            }
            if (awake != null && awake.isHeld()) {
                awake.release();
            }
        }
    }

    private Notification notification(String words) {
        getSystemService(NotificationManager.class).createNotificationChannel(
                new NotificationChannel(CHANNEL, "Pool work", NotificationManager.IMPORTANCE_LOW));
        PendingIntent off = PendingIntent.getService(this, 0, new Intent(this, getClass()).setAction(DISABLE),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("Poolhouse phone worker")
                .setContentText(words)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Turn off", off).build())
                .build();
    }
}
