package app.poolhouse.phone;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.LinearLayout;
import org.json.JSONObject;
import java.security.KeyPair;
import java.security.SecureRandom;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The home of the app: before this phone is added, one button that scans the code; after, the requests that wait for the person,
 * newest first, each one tap from its whole text. The list is kept current by a request the device holds open (it answers when
 * anything changes), and a notification brings the same request while the app is closed.
 */
public final class MainActivity extends Activity {
    private static final int SCAN = 11;
    private static final int NOTIFY = 12;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private PhoneStore store;
    private Ui ui;
    private LinearLayout list;
    private volatile boolean visible;
    private volatile long generation;
    private String received;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        store = PhoneStore.of(this);
        take(getIntent());
    }

    @Override public void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        take(intent);
        if (visible && !store.added()) addScreen();
    }

    private void take(Intent intent) {
        received = intent == null || intent.getData() == null ? null : intent.getData().toString();
        if (received != null && received.length() > 4096) received = null;
        if (intent != null) intent.setData(null);
    }

    @Override public void onStart() {
        super.onStart();
        visible = true;
        start();
    }

    private void start() {
        long epoch = ++generation;
        if (!store.added()) { addScreen(); return; }
        home();
        askForNotifications();
        worker.execute(() -> keepCurrent(epoch));
    }

    @Override public void onStop() {
        visible = false;
        generation++;
        received = null;
        super.onStop();
    }

    @Override public void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private void askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, NOTIFY);
        }
    }

    // -- before this phone is added -------------------------------------------------------------------------------

    private void addScreen() {
        ui = Ui.page(this, "Add this phone", "Then your agents ask you here, and you approve with your fingerprint.");
        ui.text("On your computer, tell your agent \"add my phone\", or run: poolhouse-workspace phone add\nThen scan the code it shows.", 16);
        ui.button("Scan the code", () -> startActivityForResult(new Intent(this, ScanActivity.class), SCAN));
        EditText paste = ui.input("Or paste the link");
        ui.button("Add with the link", () -> addPhone(paste.getText().toString().trim()));
        ui.button("Chat with your computer", () -> startActivity(new Intent(this, ChatActivity.class)));
        if (received != null && received.startsWith(AddLink.PREFIX)) { paste.setText(received); received = null; }
    }

    @Override public void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        String link = code == SCAN && result == RESULT_OK && data != null ? data.getStringExtra("link") : null;
        if (link == null || link.length() > 4096) return;
        if (link.startsWith(AddLink.PREFIX)) { if (!store.added()) addPhone(link); }
        else startActivity(new Intent(this, ChatActivity.class).setData(android.net.Uri.parse(link)));
    }

    private void addPhone(String text) {
        final long epoch = ++generation;
        ui.say("Checking the code...");
        worker.execute(() -> {
            try {
                AddLink link = AddLink.parse(text, System.currentTimeMillis() / 1000);
                NodeApi api = new NodeApi(Transport.pinned(), link.pin, link.endpoints);
                JSONObject hello = api.hello();
                ApprovalKey.create(this);
                KeyPair push = PhoneStore.newPushKey();
                byte[] pushSecret = new byte[16];
                new SecureRandom().nextBytes(pushSecret);
                String name = Build.MODEL == null || !Build.MODEL.matches("[\\x20-\\x7e]{1,40}") ? "Android phone" : Build.MODEL.trim();
                JSONObject body = AddRequest.body(link, name, ApprovalKey.publicPoint(), WebPushCipher.point(push.getPublic()), pushSecret, hello.getLong("now_ms"));
                byte[] proof = AddRequest.proofBytes(hello.getString("device"), body);
                runOnUiThread(() -> new BiometricAuthorizer(this).sign(proof, "Add this phone", new Authorizer.Result() {
                    @Override public void signed(byte[] der) {
                        worker.execute(() -> finishAdding(epoch, api, hello, name, body, der, push, pushSecret, link));
                    }

                    @Override public void failed(String sentence) {
                        ApprovalKey.forget();
                        runOnUiThread(() -> ui.say(sentence));
                    }
                }));
            } catch (IllegalStateException | IllegalArgumentException refused) {
                runOnUiThread(() -> { if (epoch == generation) ui.say(refused.getMessage()); });
            } catch (NodeError refused) {
                ApprovalKey.forget();
                runOnUiThread(() -> { if (epoch == generation) ui.say(refused.getMessage()); });
            } catch (Exception failure) {
                ApprovalKey.forget();
                runOnUiThread(() -> { if (epoch == generation) ui.say("This phone could not reach your computer. Check that both are on the same network and scan the code again."); });
            }
        });
    }

    private void finishAdding(long epoch, NodeApi api, JSONObject hello, String name, JSONObject body, byte[] der, KeyPair push, byte[] pushSecret, AddLink link) {
        try {
            JSONObject added = api.add(AddRequest.withProof(body, der));
            store.keepAdded(link, hello.getString("device"), hello.optString("name", "your computer"), added.getString("phone"), name, added.getString("token"), push, pushSecret);
            store.set("state", added.getString("state"));
            store.set("words", added.optString("words", ""));
            store.learnClock(hello.getLong("now_ms"));
            runOnUiThread(() -> {
                if (epoch == generation) start();
            });
        } catch (NodeError refused) {
            ApprovalKey.forget();
            runOnUiThread(() -> ui.say(refused.getMessage()));
        } catch (Exception failure) {
            ApprovalKey.forget();
            runOnUiThread(() -> ui.say("This phone could not be added: the computer did not answer. Scan the code again."));
        }
    }

    // -- after ------------------------------------------------------------------------------------------------------

    private void home() {
        boolean waiting = store.waitingForApproval();
        ui = Ui.page(this, store.deviceName().isEmpty() ? "poolhouse" : store.deviceName(), waiting
                ? "Waiting for one of your phones to approve this one. Its key: " + store.text("words")
                : "Requests from your agents wait here until you answer them.");
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        ui.content.addView(list, new LinearLayout.LayoutParams(-1, -2));
        ui.button("Phones and notifications", () -> startActivity(new Intent(this, PhonesActivity.class)));
        ui.button("Chat with your computer", () -> startActivity(new Intent(this, ChatActivity.class)));
        ui.say("Looking for requests...");
    }

    private void keepCurrent(long epoch) {
        long version = -1;
        while (visible && epoch == generation) {
            try {
                NodeApi api = store.api();
                if (store.waitingForApproval()) {
                    if ("active".equals(api.status(store.token()).optString("state"))) { store.set("state", "active"); runOnUiThread(this::start); return; }
                    Thread.sleep(3000);
                    continue;
                }
                JSONObject got = version < 0 ? api.list(store.token()) : api.waitFor(store.token(), version, 10000);
                version = got.getLong("version");
                store.learnClock(got.getLong("now_ms"));
                List<ApprovalView> views = ApprovalView.all(got.getJSONArray("approvals"));
                runOnUiThread(() -> { if (visible && epoch == generation) show(views); });
            } catch (SecurityException removed) {
                runOnUiThread(() -> { if (visible && epoch == generation) ui.say("This phone was removed from your computer. Add it again to approve from it."); });
                return;
            } catch (InterruptedException stopped) {
                return;
            } catch (Exception unreachable) {
                runOnUiThread(() -> { if (visible && epoch == generation) ui.say("Your computer is not reachable. Requests show here again when it is."); });
                try { Thread.sleep(5000); } catch (InterruptedException stopped) { return; }
            }
        }
    }

    private void show(List<ApprovalView> views) {
        list.removeAllViews();
        int waiting = 0;
        for (ApprovalView view : views) {
            if (!view.pending()) { Notifier.forget(this, view.id); continue; }
            waiting++;
            Notifier.keep(this, view);
            ApprovalText parts = view.parts();
            android.widget.Button card = new android.widget.Button(this);
            card.setAllCaps(false);
            card.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
            card.setText(view.title + "\n" + parts.level().toUpperCase(java.util.Locale.ROOT) + (parts.flags.isEmpty() ? "" : "  -  Check this one: " + parts.flags.get(0)));
            card.setOnClickListener(v -> startActivity(new Intent(this, ApprovalActivity.class).putExtra("id", view.id)));
            list.addView(card, new LinearLayout.LayoutParams(-1, -2));
        }
        ui.say(waiting == 0 ? "Nothing is waiting for you." : waiting + (waiting == 1 ? " request is" : " requests are") + " waiting for you.");
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == NOTIFY && results.length > 0 && results[0] != PackageManager.PERMISSION_GRANTED && ui != null) {
            ui.say("Notifications are off, so requests show only while this app is open.");
        }
    }
}
