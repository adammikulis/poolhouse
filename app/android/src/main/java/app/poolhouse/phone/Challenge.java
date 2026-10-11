package app.poolhouse.phone;

import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

/**
 * What the node asks a key to sign for one request, and what the phone checks before it asks the person's fingerprint.
 *
 * The bytes are a domain line and the canonical JSON of the node's challenge with the decision and the key added; the node
 * rebuilds exactly these bytes, so a signature over any other text, node or decision is refused there.
 */
final class Challenge {
    static final String DOMAIN = "poolhouse/approval/v1\n";
    private final JSONObject object;

    Challenge(JSONObject object) {
        this.object = object;
    }

    String text() {
        return object.optString("text", "");
    }

    String id() {
        return object.optString("id", "");
    }

    long expiresMs() {
        return object.optLong("expires_ms", 0);
    }

    /** The bytes the key whose fingerprint is {@code key} signs to give {@code decision} (approve or deny). */
    byte[] bytes(String decision, String key) throws Exception {
        if (!decision.equals("approve") && !decision.equals("deny")) throw new IllegalArgumentException("A decision is approve or deny.");
        JSONObject whole = new JSONObject(object.toString());
        whole.put("decision", decision).put("key", key);
        return (DOMAIN + Canonical.of(whole)).getBytes(StandardCharsets.UTF_8);
    }

    /**
     * Refuse a challenge that is not for this node, is not the text on the screen, has ended by the node's clock, or is
     * not a request of the shape the node writes. Nothing is signed for one of these.
     */
    void check(String node, String shown, long nodeNowMs) {
        if (object.optInt("v", 0) != 1 || !"approval".equals(object.optString("op"))) throw new SecurityException("That is not a request this app can answer.");
        if (!object.optString("node").equals(node)) throw new SecurityException("That request is for another device, so nothing was signed.");
        if (!text().equals(shown)) throw new SecurityException("The text on the screen is not the text of the request, so nothing was signed.");
        if (expiresMs() <= nodeNowMs) throw new SecurityException("That request has ended.");
        if (!object.optString("nonce").matches("[0-9a-f]{32}") || !id().matches("ap-[0-9a-f]{16}")) throw new SecurityException("That request is malformed.");
    }
}
