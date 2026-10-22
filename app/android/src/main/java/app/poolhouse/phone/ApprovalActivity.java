package app.poolhouse.phone;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
import android.widget.TextView;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * One request in full: what looks off first, the pool line, the risk, what the person said, who asks, how the node judged it, what
 * is asked and how far it reaches, what it cannot do, why, what a deny does and when it ends, each line exactly as the node wrote it
 * and the key signs it. Each answer asks for the fingerprint once; an approval of what is not routine then asks which number the agent
 * showed, and a scope declined before takes one more deliberate confirmation first. The screen is never shown on a locked phone.
 */
public final class ApprovalActivity extends Activity {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler clock = new Handler(Looper.getMainLooper());
    private PhoneStore store;
    private Ui ui;
    private ApprovalView view;
    private String prompt = "";
    private TextView countdown;
    private volatile boolean visible;
    private boolean prompted;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        setShowWhenLocked(false);
        store = PhoneStore.of(this);
    }

    @Override public void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        prompted = false;
    }

    @Override public void onStart() {
        super.onStart();
        visible = true;
        String id = getIntent().getStringExtra("id");
        prompt = getIntent().getStringExtra("prompt") == null ? "" : getIntent().getStringExtra("prompt");
        view = id == null ? null : Notifier.kept(this, id);
        if (view != null) render();
        else { ui = Ui.page(this, "Request", ""); ui.say("Looking for the request..."); }
        if (id != null) worker.execute(() -> fetch(id));
    }

    @Override public void onStop() {
        visible = false;
        clock.removeCallbacksAndMessages(null);
        super.onStop();
    }

    @Override public void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private void fetch(String id) {
        try {
            ApprovalView fresh = new ApprovalView(store.api().get(store.token(), id));
            runOnUiThread(() -> {
                if (!visible) return;
                view = fresh;
                Notifier.keep(this, fresh);
                render();
            });
        } catch (SecurityException removed) {
            runOnUiThread(() -> ui.say("This phone was removed from your computer."));
        } catch (Exception unreachable) {
            runOnUiThread(() -> { if (view == null) ui.say("Your computer is not reachable, and this request is not saved on the phone. Open the app when it is."); });
        }
    }

    private int colour(String line) {
        if (line.startsWith("FLAG:")) return Ui.WARN;
        if (line.startsWith("RISK: IRREVERSIBLE")) return Ui.DANGER;
        if (line.startsWith("RISK: SENSITIVE")) return Ui.WARN;
        if (line.startsWith("POOL:")) return Ui.CALM;
        return Color.BLACK;
    }

    private void render() {
        ui = Ui.page(this, view.title, "");
        StringBuilder shown = new StringBuilder();
        for (String line : view.text.split("\n")) {
            ui.line(line, colour(line), line.startsWith("FLAG:") || line.startsWith("POOL:") || line.startsWith("RISK:"));
            shown.append(shown.length() == 0 ? "" : "\n").append(line);
        }
        List<String> wants = Wants.of(view.challenge);
        if (!wants.isEmpty()) {
            ui.line("What the signed request itself asks, read from the bytes your key signs:", Ui.CALM, true);
            for (String want : wants) ui.line(want, Color.BLACK, false);
        }
        showListing();
        countdown = ui.text("", 15);
        final String onScreen = shown.toString();
        boolean open = view.pending();
        String[] labels = view.labels();
        ui.button(labels[0], () -> approve(onScreen)).setEnabled(open);
        if (!labels[1].isEmpty()) ui.button(labels[1], () -> decide("deny", onScreen)).setEnabled(open);
        if (!labels[2].isEmpty()) ui.button(labels[2], () -> decide("block", onScreen)).setEnabled(open);
        if (!open) ui.say(view.state.equals("approved") ? "This request was approved." : view.state.equals("denied") ? "This request was denied." : "This request has ended.");
        tick();
        boolean direct = open && !prompted && ((prompt.equals("deny") && !labels[1].isEmpty()) || (prompt.equals("block") && !labels[2].isEmpty())
                || (prompt.equals("approve") && !view.opensScreen()));
        if (direct) {
            prompted = true;
            decide(prompt, onScreen);
        }
    }

    /** For a request that lists a phone as an admin key: what the act itself says when it checks, or why it does not. */
    private void showListing() {
        try {
            AdminAddAct act = AdminAddAct.of(view.challenge, store.nodeNowMs());
            if (act == null) return;
            ui.line("What the signed listing itself says, read from the bytes your key signs:", Ui.CALM, true);
            ui.line(act.card, Color.BLACK, false);
        } catch (SecurityException refused) {
            ui.line(refused.getMessage(), Ui.WARN, true);
        }
    }

    private void tick() {
        clock.removeCallbacksAndMessages(null);
        long left = view.expiresMs - store.nodeNowMs();
        countdown.setText(left <= 0 ? "This request has ended." : "Ends in " + (left / 60000) + " min " + ((left / 1000) % 60) + " s");
        if (left > 0 && visible) clock.postDelayed(this::tick, 1000);
    }

    /** Approve: a scope that was declined before is confirmed first, as its own deliberate step, and then approved again. */
    private void approve(String onScreen) {
        long repeat = view.challenge.repeat();
        if (repeat == 0) {
            decide("approve", onScreen);
            return;
        }
        new AlertDialog.Builder(this).setTitle("Declined " + repeat + " time(s) before")
                .setMessage("You declined this " + repeat + " time(s) before. Approve it anyway?")
                .setNegativeButton("No", null).setPositiveButton("Approve anyway", (dialog, which) -> decide("approve_repeat", onScreen)).show();
    }

    /** Ask which number the agent showed, after the fingerprint; leaving the question sends nothing. */
    private void choose(List<String> options, java.util.function.Consumer<String> chosen, Runnable cancelled) {
        runOnUiThread(() -> new AlertDialog.Builder(this).setTitle("Which number did your agent show you?")
                .setItems(options.toArray(new String[0]), (dialog, which) -> worker.execute(() -> chosen.accept(options.get(which))))
                .setOnCancelListener(dialog -> cancelled.run()).show());
    }

    private void decide(String decision, String onScreen) {
        ui.say("Waiting for your fingerprint...");
        Decider decider = new Decider(store.api(), new BiometricAuthorizer(this), store.device());
        decider.decide(view, decision, onScreen, store.nodeNowMs(), this::choose, new Decider.Done() {
            @Override public void answered(String state) {
                runOnUiThread(() -> {
                    Notifier.forget(ApprovalActivity.this, view.id);
                    ui.say(state.equals("approved") ? "Approved." : "Denied.");
                    finish();
                });
            }

            @Override public void failed(String sentence) {
                runOnUiThread(() -> ui.say(sentence));
            }
        });
    }
}
