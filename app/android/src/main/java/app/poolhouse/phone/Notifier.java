package app.poolhouse.phone;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.drawable.Icon;
import android.net.Uri;
import android.os.Build;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/**
 * The notification of a request: the lock screen shows only that an approval is waiting from an agent; once the phone is unlocked
 * the title and the whole text show, with Approve and Deny. Approve never signs: it opens the screen or the fingerprint prompt,
 * and for a request that is not routine it opens the whole text first.
 */
final class Notifier {
    static final String CHANNEL = "approvals";
    private Notifier() { }

    static int idOf(String approval) {
        return approval.hashCode();
    }

    private static File cache(Context context, String id) {
        return new File(context.getNoBackupFilesDir(), "pending-" + id + ".json");
    }

    /** Keep the request a push carried, sealed, so the screen opens on it at once even when the device cannot be reached yet. */
    static void keep(Context context, ApprovalView view) {
        try {
            Files.write(cache(context, view.id).toPath(), PhoneStore.of(context).seal(view.raw.toString().getBytes(StandardCharsets.UTF_8), view.id));
        } catch (Exception unwritten) {
            return;
        }
    }

    static ApprovalView kept(Context context, String id) {
        try {
            return new ApprovalView(new JSONObject(new String(PhoneStore.of(context).unseal(Files.readAllBytes(cache(context, id).toPath()), id), StandardCharsets.UTF_8)));
        } catch (Exception none) {
            return null;
        }
    }

    static void forget(Context context, String id) {
        cache(context, id).delete();
        context.getSystemService(NotificationManager.class).cancel(idOf(id));
    }

    /**
     * What a tap opens. A PendingIntent is told apart by its request code and its data, never by its extras, so each (request, button)
     * pair has its own data: two requests whose ids hash near each other cannot take over each other's Approve or Deny.
     */
    private static PendingIntent open(Context context, PushPayload payload, String prompt) {
        Uri data = new Uri.Builder().scheme("poolhouse").authority("approval").appendPath(payload.id).appendPath(prompt.isEmpty() ? "read" : prompt).build();
        Intent intent = new Intent(context, ApprovalActivity.class).setData(data).putExtra("id", payload.id).putExtra("prompt", prompt)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private static Notification.Action action(String label, PendingIntent intent) {
        Notification.Action.Builder builder = new Notification.Action.Builder(Icon.createWithResource("android", android.R.drawable.ic_menu_send), label, intent);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) builder.setAuthenticationRequired(true);
        return builder.build();
    }

    /** Show {@code model} for {@code payload}. */
    static void show(Context context, PushPayload payload, NotificationModel model) {
        if (payload.view != null) keep(context, payload.view);
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        NotificationChannel channel = new NotificationChannel(CHANNEL, "Approvals waiting", NotificationManager.IMPORTANCE_HIGH);
        channel.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        channel.setDescription("Requests from your agents that wait for your approval");
        manager.createNotificationChannel(channel);
        Notification hidden = new Notification.Builder(context, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(model.publicTitle)
                .setContentText(model.publicText).setVisibility(Notification.VISIBILITY_PUBLIC).setLocalOnly(true).build();
        PendingIntent read = open(context, payload, "");
        Notification shown = new Notification.Builder(context, CHANNEL).setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(model.title)
                .setContentText(model.collapsed).setStyle(new Notification.BigTextStyle().bigText(model.body)).setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(hidden).setCategory(Notification.CATEGORY_MESSAGE).setAutoCancel(true).setOnlyAlertOnce(true).setTimeoutAfter(model.timeoutMs)
                .setLocalOnly(true).setContentIntent(read)
                .addAction(action("Approve", model.approveOpensScreen ? read : open(context, payload, "approve")))
                .addAction(action("Deny", open(context, payload, "deny"))).build();
        manager.notify(idOf(payload.id), shown);
    }
}
