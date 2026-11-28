package app.poolhouse.node;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * The phone as a pool device inside the app that holds it: {@code poolhouse-node} as a foreground service, off until
 * {@link #start} is called and off again after {@link #stop}. The node is the one every device runs; it runs no code
 * another device sends (that needs an isolated runner, which plugs in at the node's job folder).
 */
public class NodeService extends Service {
    private static final String CHANNEL = "node";
    private static final String START = "app.poolhouse.node.START";
    private static final String STOP = "app.poolhouse.node.STOP";

    private static NodeProcess node;
    private static PowerManager.WakeLock awake;

    /** Switch node mode on: the node runs, with the screen off too, until {@link #stop}. */
    public static void start(Context context) {
        context.startForegroundService(new Intent(context, NodeService.class).setAction(START));
    }

    /** Switch node mode off: the node's process ends and nothing of it listens. */
    public static void stop(Context context) {
        context.startForegroundService(new Intent(context, NodeService.class).setAction(STOP));
    }

    public static boolean enabled(Context context) {
        return context.getSharedPreferences("node", MODE_PRIVATE).getBoolean("enabled", false);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, notification());
        }
        String action = intent == null ? null : intent.getAction();
        if (START.equals(action) || STOP.equals(action)) {
            getSharedPreferences("node", MODE_PRIVATE).edit().putBoolean("enabled", START.equals(action)).apply();
        }
        if (!enabled(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        synchronized (NodeService.class) {
            if (node == null) {
                awake = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "poolhouse:node");
                awake.acquire();
                node = new NodeProcess(getApplicationContext());
                node.start();
            }
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        synchronized (NodeService.class) {
            if (node != null) {
                node.end();
                node = null;
                awake.release();
            }
        }
        super.onDestroy();
    }

    private Notification notification() {
        getSystemService(NotificationManager.class).createNotificationChannel(
                new NotificationChannel(CHANNEL, "Pool node", NotificationManager.IMPORTANCE_LOW));
        PendingIntent off = PendingIntent.getService(this, 0, new Intent(this, getClass()).setAction(STOP),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("This phone is a pool device")
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Turn off", off).build())
                .build();
    }
}
