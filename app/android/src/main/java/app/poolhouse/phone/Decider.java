package app.poolhouse.phone;

import org.json.JSONObject;

/**
 * Answers a request: checks the challenge against what is on the screen and this device, asks the person (one fingerprint),
 * and gives the node the signature. Nothing is signed for a request that fails a check, and no step asks the person twice.
 */
final class Decider {
    interface Done {
        void answered(String state);

        void failed(String sentence);
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

    /** Answer {@code view} with {@code decision}, having shown the person {@code shown}; {@code nodeNowMs} is the node's clock. */
    void decide(ApprovalView view, String decision, String shown, long nodeNowMs, Done done) {
        try {
            view.challenge.check(device, shown, nodeNowMs);
            String fingerprint = Hex.sha256(key.publicPoint());
            byte[] bytes = view.challenge.bytes(decision, fingerprint);
            key.sign(bytes, decision.equals("approve") ? "Approve" : "Deny", new Authorizer.Result() {
                @Override public void signed(byte[] der) {
                    try {
                        done.answered(api.decide(body(view, decision, fingerprint, der)).optString("state", "approved"));
                    } catch (NodeError refused) {
                        done.failed(refused.getMessage());
                    } catch (Exception failure) {
                        done.failed("The device could not be reached, so nothing was changed. Try again when it is.");
                    }
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
}
