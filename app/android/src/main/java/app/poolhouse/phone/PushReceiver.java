package app.poolhouse.phone;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * Where a push distributor's broadcasts land: a new address, a message, a refusal or the end of the registration. A broadcast
 * that does not carry this app's instance token is dropped, so another app cannot hand this phone an address or a message.
 */
public final class PushReceiver extends BroadcastReceiver {
    static final String NEW_ENDPOINT = "org.unifiedpush.android.connector.NEW_ENDPOINT";
    static final String MESSAGE = "org.unifiedpush.android.connector.MESSAGE";
    static final String FAILED = "org.unifiedpush.android.connector.REGISTRATION_FAILED";
    static final String UNREGISTERED = "org.unifiedpush.android.connector.UNREGISTERED";

    static byte[] body(Intent intent) {
        byte[] raw = intent.getByteArrayExtra("bytesMessage");
        if (raw != null) return raw;
        String text = intent.getStringExtra("message");
        if (text == null) return new byte[0];
        try {
            return Base64.getDecoder().decode(text);
        } catch (IllegalArgumentException notEncoded) {
            return text.getBytes(StandardCharsets.UTF_8);
        }
    }

    @Override public void onReceive(Context context, Intent intent) {
        PhoneStore store = PhoneStore.of(context);
        String token = intent.getStringExtra("token");
        if (token == null || store.instance().isEmpty() || !java.security.MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8), store.instance().getBytes(StandardCharsets.UTF_8))) return;
        PendingResult pending = goAsync();
        new Thread(() -> {
            try {
                PushHandler handler = new PushHandler(store, (payload, model) -> Notifier.show(context, payload, model), endpoint -> {
                    store.api().setPushAddress(store.token(), endpoint);
                    store.set("push_endpoint", endpoint);
                }, store::nodeNowMs);
                String action = intent.getAction() == null ? "" : intent.getAction();
                if (action.equals(NEW_ENDPOINT) && intent.getStringExtra("endpoint") != null) handler.address(intent.getStringExtra("endpoint"));
                else if (action.equals(MESSAGE)) handler.message(body(intent));
                else if (action.equals(FAILED) || action.equals(UNREGISTERED)) handler.failed("The push distributor stopped.");
            } finally {
                pending.finish();
            }
        }).start();
    }
}
