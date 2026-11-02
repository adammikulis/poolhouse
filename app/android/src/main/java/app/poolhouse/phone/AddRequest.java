package app.poolhouse.phone;

import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

/**
 * What this phone sends to be added: its name, its approval key, its push key, and the proof that the approval key made the request;
 * for a code that carries terms, also the key's acceptance of being listed as an admin key under them.
 */
final class AddRequest {
    static final String DOMAIN = "poolhouse/phone-add/v1\n";
    static final String ACCEPT_DOMAIN = "poolhouse/pool-accept/v2\n";
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

    /**
     * The bytes a key signs to accept being listed as a strong admin key of {@code person} from epoch {@code since}, in the pool
     * {@code anchor} whose latest charter is {@code prev}. The device and the approving phone rebuild the same bytes to check the acceptance.
     */
    static byte[] acceptanceBytes(String key, String person, long since, String anchor, String prev) throws Exception {
        JSONObject terms = new JSONObject().put("v", 2).put("key", key).put("alg", "p256").put("role", "admin").put("strength", "strong")
                .put("person", person).put("since", since).put("anchor", anchor).put("prev", prev);
        return (ACCEPT_DOMAIN + Canonical.of(terms)).getBytes(StandardCharsets.UTF_8);
    }

    /** The acceptance of the terms a code carries, by the approval key whose point is {@code approvalPoint}. */
    static byte[] acceptanceBytes(AddLink.Admit terms, byte[] approvalPoint) throws Exception {
        return acceptanceBytes(Hex.of(approvalPoint), terms.person, terms.since, terms.pool, terms.prev);
    }

    /** The request with the key's acceptance of being listed, in the one low-s form the pool takes. */
    static JSONObject withAcceptance(JSONObject body, byte[] der) throws Exception {
        return new JSONObject(body.toString()).put("acceptance", Hex.of(P256Signature.normalize(der)));
    }
}
