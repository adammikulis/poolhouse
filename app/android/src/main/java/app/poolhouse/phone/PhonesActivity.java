package app.poolhouse.phone;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Bundle;
import android.view.WindowManager;
import android.widget.ImageView;
import android.widget.LinearLayout;
import com.google.zxing.BarcodeFormat;
import com.google.zxing.EncodeHintType;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.qrcode.QRCodeWriter;
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel;
import org.json.JSONObject;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The phones this device holds and how this one wakes: add another phone (a code to scan on it), remove one (a request that a
 * phone that stays approves with its fingerprint), and turn on the wake-up that brings a request while the app is closed.
 */
public final class PhonesActivity extends Activity {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private PhoneStore store;
    private Ui ui;
    private volatile boolean visible;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        store = PhoneStore.of(this);
    }

    @Override public void onStart() {
        super.onStart();
        visible = true;
        ui = Ui.page(this, "Phones", "The phones that can approve for " + (store.deviceName().isEmpty() ? "your computer" : store.deviceName()) + ".");
        ui.say("Looking...");
        worker.execute(this::load);
    }

    @Override public void onStop() {
        visible = false;
        super.onStop();
    }

    @Override public void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private void load() {
        try {
            List<JSONObject> held = store.api().phones(store.token());
            runOnUiThread(() -> { if (visible) show(held); });
        } catch (SecurityException removed) {
            runOnUiThread(() -> ui.say("This phone was removed from your computer."));
        } catch (Exception unreachable) {
            runOnUiThread(() -> ui.say("Your computer is not reachable."));
        }
    }

    private void show(List<JSONObject> held) {
        ui = Ui.page(this, "Phones", "The phones that can approve for " + (store.deviceName().isEmpty() ? "your computer" : store.deviceName()) + ".");
        for (JSONObject phone : held) {
            if ("removed".equals(phone.optString("state"))) continue;
            String fingerprint = phone.optString("fingerprint", "");
            String words = fingerprint.length() >= 12 ? fingerprint.substring(0, 4) + " " + fingerprint.substring(4, 8) + " " + fingerprint.substring(8, 12) : "";
            boolean here = phone.optBoolean("this");
            ui.line(phone.optString("name") + (here ? " (this phone)" : "") + (phone.optString("state").equals("pending") ? " - waiting to be approved" : ""), Ui.INK, true);
            ui.text("Key " + words + (phone.optBoolean("push") ? "  -  wake-up on" : "  -  wake-up off"), 14);
            if (phone.optString("state").equals("active")) ui.button("Remove " + phone.optString("name"), () -> confirmRemove(phone.optString("id"), phone.optString("name")));
        }
        ui.button("Add another phone", this::offer);
        String distributor = UnifiedPushChannel.distributor(this);
        ui.text(distributor.isEmpty()
                ? "Requests show while this app is open. To get them while it is closed, install a push app such as ntfy, then come back here."
                : "Wake-up through " + distributor + " brings a request while this app is closed. The push service sees only scrambled bytes.", 14);
        if (!distributor.isEmpty()) ui.button("Turn on wake-up", () -> {
            new UnifiedPushChannel(this, store.instance()).register();
            ui.say("Asked " + distributor + " for an address.");
        });
        ui.notice.bringToFront();
    }

    private void confirmRemove(String id, String name) {
        new AlertDialog.Builder(this).setTitle("Remove " + name + "?").setMessage("It stops being able to approve at once. One fingerprint from a phone that stays confirms it.")
                .setNegativeButton("Keep", null).setPositiveButton("Remove", (dialog, which) -> worker.execute(() -> remove(id))).show();
    }

    private void remove(String id) {
        try {
            ApprovalView request = new ApprovalView(store.api().removePhone(store.token(), id));
            runOnUiThread(() -> new Decider(store.api(), new BiometricAuthorizer(this), store.device()).decide(request, "approve", request.text, store.nodeNowMs(), new Decider.Done() {
                @Override public void answered(String state) { runOnUiThread(() -> { ui.say("Removed."); worker.execute(PhonesActivity.this::load); }); }

                @Override public void failed(String sentence) { runOnUiThread(() -> ui.say(sentence)); }
            }));
        } catch (NodeError refused) {
            runOnUiThread(() -> ui.say(refused.getMessage()));
        } catch (Exception failure) {
            runOnUiThread(() -> ui.say("Your computer did not answer, so nothing was changed."));
        }
    }

    private void offer() {
        ui.say("Making a code...");
        worker.execute(() -> {
            try {
                String link = store.api().offer(store.token());
                Bitmap code = qr(link);
                runOnUiThread(() -> {
                    ImageView image = new ImageView(this);
                    image.setImageBitmap(code);
                    image.setAdjustViewBounds(true);
                    image.setContentDescription("The code that adds another phone");
                    ui.content.addView(image, new LinearLayout.LayoutParams(-1, -2));
                    ui.say("Scan this on the new phone within ten minutes. One of your phones then asks you to approve it.");
                });
            } catch (NodeError refused) {
                runOnUiThread(() -> ui.say(refused.getMessage()));
            } catch (Exception failure) {
                runOnUiThread(() -> ui.say("Your computer did not answer, so no code was made."));
            }
        });
    }

    static Bitmap qr(String text) throws Exception {
        BitMatrix matrix = new QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, 640, 640, Map.of(EncodeHintType.ERROR_CORRECTION, ErrorCorrectionLevel.M, EncodeHintType.MARGIN, 4));
        int[] pixels = new int[matrix.getWidth() * matrix.getHeight()];
        for (int y = 0; y < matrix.getHeight(); y++) for (int x = 0; x < matrix.getWidth(); x++) pixels[y * matrix.getWidth() + x] = matrix.get(x, y) ? Color.BLACK : Color.WHITE;
        return Bitmap.createBitmap(pixels, matrix.getWidth(), matrix.getHeight(), Bitmap.Config.ARGB_8888);
    }
}
