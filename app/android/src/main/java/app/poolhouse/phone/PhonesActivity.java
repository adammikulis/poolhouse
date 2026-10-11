package app.poolhouse.phone;

import android.app.Activity;
import android.content.Intent;
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
            String words = Hex.words(fingerprint);
            boolean here = phone.optBoolean("this");
            ui.line(phone.optString("name") + (here ? " (this phone)" : "") + (phone.optString("state").equals("pending") ? " - waiting to be approved" : ""), Ui.INK, true);
            ui.text("Key " + words + (phone.optBoolean("push") ? "  -  wake-up on" : "  -  wake-up off"), 14);
            if (phone.optString("state").equals("active")) ui.button("Remove " + phone.optString("name"), () -> askToRemove(phone.optString("id")));
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

    /**
     * Ask the node to take a phone off. What a key signs for that is a request like any other, so it opens on the same screen:
     * the whole text, then Approve or Deny behind one fingerprint. Nothing about a removal is signed that was not shown.
     */
    private void askToRemove(String id) {
        ui.say("Asking your computer...");
        worker.execute(() -> remove(id));
    }

    private void remove(String id) {
        try {
            ApprovalView request = new ApprovalView(store.api().removePhone(store.token(), id));
            Notifier.keep(this, request);
            runOnUiThread(() -> startActivity(new Intent(this, ApprovalActivity.class).putExtra("id", request.id).putExtra("prompt", "")));
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
