package app.poolhouse.phone;

import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

/** What this phone sends to be added: its name, its approval key, its push key, and the proof that the approval key made the request. */
final class AddRequest {
    static final String DOMAIN = "poolhouse/phone-add/v1\n";
    private AddRequest() { }

    /** The request without its proof: the code's secret, the phone's name and keys, and the time by the phone's clock. */
    static JSONObject body(AddLink link, String name, byte[] approvalPoint, byte[] pushPoint, byte[] pushSecret, long nowMs) throws Exception {
        return new JSONObject().put("secret", link.secret).put("name", name).put("key", Hex.of(approvalPoint)).put("p256dh", Hex.of(pushPoint))
                .put("auth", Hex.of(pushSecret)).put("at_ms", nowMs);
    }

    /** The bytes the approval key signs to prove the request for the device whose certificate fingerprint is {@code device}. */
    static byte[] proofBytes(String device, JSONObject body) throws Exception {
        JSONObject proof = new JSONObject().put("op", "phone_add").put("node", device).put("name", body.getString("name")).put("key", body.getString("key"))
                .put("push", body.getString("p256dh")).put("auth", body.getString("auth")).put("at_ms", body.getLong("at_ms"));
        return (DOMAIN + Canonical.of(proof)).getBytes(StandardCharsets.UTF_8);
    }

    static JSONObject withProof(JSONObject body, byte[] der) throws Exception {
        return new JSONObject(body.toString()).put("proof", Hex.of(der));
    }
}
