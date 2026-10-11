package app.poolhouse.phone;

import org.json.JSONObject;
import java.util.List;
import java.util.function.Consumer;

/**
 * Answers a request: checks the challenge against what is on the screen and this device, asks the person (one fingerprint),
 * asks which number the agent showed when the request has one, and gives the node the signature. Nothing is signed for a request
 * that fails a check, and no step asks the person's fingerprint twice.
 */
final class Decider {
    interface Done {
        void answered(String state);

        void failed(String sentence);
    }

    /** Asks the person which of the numbers the agent showed; exactly one of {@code chosen} and {@code cancelled} is called, once. */
    interface Picker {
        void choose(List<String> options, Consumer<String> chosen, Runnable cancelled);
    }

    private final NodeApi api;
    private final Authorizer key;
    private final String device;

    Decider(NodeApi api, Authorizer key, String device) {
        this.api = api;
        this.key = key;
        this.device = device;
    }

    /** The body of the `decide` request for a signature {@code der} over the bytes of {@code view} for {@code decision}. */
    static JSONObject body(ApprovalView view, String decision, String fingerprint, byte[] der) throws Exception {
        return new JSONObject().put("id", view.id).put("decision", decision).put("key", fingerprint).put("sig", Hex.of(der))
                .put("text_sha256", Hex.sha256(view.text));
    }

    /** The same with the number the person chose: it is not signed, the node checks it against the one it gave the agent. */
    static JSONObject body(ApprovalView view, String decision, String fingerprint, byte[] der, String pick) throws Exception {
        return body(view, decision, fingerprint, der).put("pick", pick);
    }

    /** Refuse what no person should be able to sign by reflex: the plain approval of a scope declined before, a block that is not offered, a number to match with nobody to ask. */
    private static void rules(ApprovalView view, String decision, Picker picker) {
        if (view.labels()[slot(decision)].isEmpty()) throw new SecurityException("That request does not offer that answer.");
        long repeat = view.challenge.repeat();
        if (decision.equals("approve") && repeat > 0) throw new SecurityException("That scope was declined " + repeat + " time(s) before: confirm that first, then approve it again.");
        if (decision.equals("approve_repeat") && repeat == 0) throw new SecurityException("That scope was not declined before, so it is approved the usual way.");
        if (decision.startsWith("approve") && !view.challenge.pick().isEmpty() && picker == null) throw new SecurityException("There is no way to choose the number, so nothing was signed.");
    }

    /** Which of the three buttons ({@code approve}, {@code deny}, {@code block}) a decision belongs to. */
    private static int slot(String decision) {
        return decision.equals("deny") ? 1 : decision.equals("block") ? 2 : 0;
    }

    /** Answer {@code view}, which needs no number to match, with {@code decision}, having shown the person {@code shown}; {@code nodeNowMs} is the node's clock. */
    void decide(ApprovalView view, String decision, String shown, long nodeNowMs, Done done) {
        decide(view, decision, shown, nodeNowMs, null, done);
    }

    /** Answer {@code view} with {@code decision}, having shown the person {@code shown}; {@code picker} asks which number the agent showed. */
    void decide(ApprovalView view, String decision, String shown, long nodeNowMs, Picker picker, Done done) {
        try {
            view.challenge.check(device, shown, nodeNowMs);
            if (!view.whole()) throw new SecurityException("That request leaves out something a person needs to decide, so nothing was signed.");
            rules(view, decision, picker);
            String fingerprint = Hex.sha256(key.publicPoint());
            byte[] bytes = view.challenge.bytes(decision, fingerprint);
            key.sign(bytes, view.labels()[slot(decision)], new Authorizer.Result() {
                @Override public void signed(byte[] der) {
                    List<String> options = view.challenge.pick();
                    if (!decision.startsWith("approve") || options.isEmpty()) {
                        send(view, decision, fingerprint, der, null, done);
                        return;
                    }
                    picker.choose(options, chosen -> send(view, decision, fingerprint, der, chosen, done),
                            () -> done.failed("Nothing was sent: no number was chosen, so the request waits."));
                }

                @Override public void failed(String sentence) {
                    done.failed(sentence);
                }
            });
        } catch (SecurityException refused) {
            done.failed(refused.getMessage());
        } catch (Exception failure) {
            done.failed("This request could not be read, so nothing was signed.");
        }
    }

    private void send(ApprovalView view, String decision, String fingerprint, byte[] der, String pick, Done done) {
        try {
            JSONObject body = pick == null ? body(view, decision, fingerprint, der) : body(view, decision, fingerprint, der, pick);
            done.answered(api.decide(body).optString("state", "approved"));
        } catch (NodeError refused) {
            done.failed(refused.getMessage());
        } catch (Exception failure) {
            done.failed("The device could not be reached, so nothing was changed. Try again when it is.");
        }
    }
}
