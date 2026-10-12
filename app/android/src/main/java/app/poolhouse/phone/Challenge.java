package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * What the node asks a key to sign for one request, and what the phone checks before it asks the person's fingerprint.
 *
 * The bytes are a domain line and the canonical JSON of the node's challenge with the decision and the key added; the node
 * rebuilds exactly these bytes, so a signature over any other text, node, decision or context is refused there. The context
 * (what the person said, the verdict, the taint, the undo window, how often the scope was declined, the numbers to choose
 * among, what a block lasts) is part of those bytes, so a challenge that leaves a part out is refused here before any prompt.
 */
final class Challenge {
    static final String DOMAIN = "poolhouse/approval/v1\n";
    static final List<String> DECISIONS = Collections.unmodifiableList(Arrays.asList("approve", "approve_repeat", "deny", "block"));
    private static final List<String> CONTEXT_KEYS = Arrays.asList("said", "verdict", "taint", "held", "prompted", "repeat", "pick", "block_ms");
    private static final List<String> ORIGINS = Arrays.asList("none", "harness", "app");
    private static final List<String> VERDICTS = Arrays.asList("matches", "beyond", "unrelated", "none");
    private static final List<String> TAINTS = Arrays.asList("unknown", "clean", "tainted");
    private static final List<String> PROMPTED = Arrays.asList("yes", "no", "unknown");
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

    /** What kind of request this is: grants an agent asked for, a phone's addition or removal, or a notice about requests kept back. */
    String kind() {
        JSONObject action = object.optJSONObject("action");
        return action == null ? "" : action.optString("kind", "");
    }

    /** The structured request the key signs: what it wants, before any prose. */
    JSONObject action() {
        return object.optJSONObject("action");
    }

    /** For a notice about requests kept back: why (held, suspended or flood). */
    String cause() {
        JSONObject action = object.optJSONObject("action");
        return action == null ? "" : action.optString("cause", "");
    }

    private JSONObject context() {
        JSONObject context = object.optJSONObject("context");
        return context == null ? new JSONObject() : context;
    }

    /** How many times the scope this asks for was declined before. */
    long repeat() {
        return context().optLong("repeat", 0);
    }

    /** What a deny and block lasts, in milliseconds; 0 when the request offers none. */
    long blockMs() {
        return context().optLong("block_ms", 0);
    }

    /** The three numbers the person chooses among after the fingerprint; empty when no number has to be matched. */
    List<String> pick() {
        List<String> out = new ArrayList<>();
        JSONArray options = context().optJSONArray("pick");
        for (int i = 0; options != null && i < options.length(); i++) out.add(options.optString(i));
        return out;
    }

    /** Whether this asks the key to sign one act of a pool (a charter's acceptance, an admit, a removal): the signature is over the act's own bytes. */
    boolean signsAct() {
        return "sign".equals(kind());
    }

    /**
     * The bytes the key signs to approve a request to sign an act: made again here from the structure the action carries, never taken from the
     * node's own hex of them, and refused unless they are what the node named and the request asks this phone's own key ({@code ownPoint}, the
     * public point in hex). A charter must list exactly one admin, this key, as a P-256 key.
     */
    byte[] actBytes(String ownPoint) throws Exception {
        JSONObject action = action();
        String step = action == null ? "" : action.optString("step");
        if (action == null || !ownPoint.equals(action.optString("key")) || !"p256-ecdsa".equals(action.optString("alg"))) {
            throw new SecurityException("That request asks a key that is not this phone's, so nothing was signed.");
        }
        String text;
        if ("act".equals(step)) {
            JSONObject act = new JSONObject(action.getJSONObject("act").toString());
            act.remove("signatures");
            text = "poolhouse/pool-act/v1\n" + Canonical.of(act);
        } else if ("found".equals(step)) {
            JSONObject charter = new JSONObject(action.getJSONObject("charter").toString());
            JSONArray admins = charter.getJSONArray("admins");
            if (admins.length() != 1 || !ownPoint.equals(admins.getJSONObject(0).optString("key")) || !"p256-ecdsa".equals(admins.getJSONObject(0).optString("alg"))) {
                throw new SecurityException("That charter does not name this phone's key as its one admin, so nothing was signed.");
            }
            JSONObject entry = new JSONObject(admins.getJSONObject(0).toString());
            for (int i = 0; i < admins.length(); i++) admins.getJSONObject(i).put("acceptance", "");
            entry.remove("acceptance");
            entry.remove("label");
            entry.put("v", 1).put("anchor", Hex.sha256(Canonical.of(charter)));
            text = "poolhouse/pool-accept/v1\n" + Canonical.of(entry);
        } else {
            throw new SecurityException("That is not a request to sign an act, so nothing was signed.");
        }
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        if (!Hex.of(bytes).equals(action.optString("message"))) throw new SecurityException("The bytes to sign are not the ones this request describes, so nothing was signed.");
        return bytes;
    }

    /** The bytes the key whose fingerprint is {@code key} signs to give {@code decision}. */
    byte[] bytes(String decision, String key) throws Exception {
        if (!DECISIONS.contains(decision)) throw new IllegalArgumentException("A decision is approve, approve again, deny or block.");
        JSONObject whole = new JSONObject(object.toString());
        whole.put("decision", decision).put("key", key);
        return (DOMAIN + Canonical.of(whole)).getBytes(StandardCharsets.UTF_8);
    }

    private static boolean one(JSONObject from, String key, List<String> allowed) {
        return allowed.contains(from.optString(key, "\u0000"));
    }

    /** Whether the context has every part the node writes, each with a value the node writes. */
    private boolean contextIsWhole() {
        JSONObject context = object.optJSONObject("context");
        if (context == null || context.length() != CONTEXT_KEYS.size()) return false;
        for (String key : CONTEXT_KEYS) if (!context.has(key)) return false;
        JSONObject said = context.optJSONObject("said");
        JSONObject held = context.optJSONObject("held");
        JSONArray pick = context.optJSONArray("pick");
        return said != null && said.length() == 2 && said.optString("quote", null) != null && one(said, "origin", ORIGINS)
                && one(context, "verdict", VERDICTS) && one(context, "taint", TAINTS) && one(context, "prompted", PROMPTED)
                && held != null && held.length() == 2 && held.optLong("act_at_ms", -1) >= 0 && held.has("cancel")
                && context.optLong("repeat", -1) >= 0 && context.optLong("block_ms", -1) >= 0 && pick != null && (pick.length() == 0 || pick.length() == 3);
    }

    /**
     * Refuse a challenge that is not for this node, is not the text on the screen, has ended by the node's clock, leaves a part of
     * the context out, or is not a request of the shape the node writes. Nothing is signed for one of these.
     */
    void check(String node, String shown, long nodeNowMs) {
        if (object.optInt("v", 0) != 1 || !"approval".equals(object.optString("op"))) throw new SecurityException("That is not a request this app can answer.");
        if (!object.optString("node").equals(node)) throw new SecurityException("That request is for another device, so nothing was signed.");
        if (!text().equals(shown)) throw new SecurityException("The text on the screen is not the text of the request, so nothing was signed.");
        if (expiresMs() <= nodeNowMs) throw new SecurityException("That request has ended.");
        if (!object.optString("nonce").matches("[0-9a-f]{32}") || !id().matches("ap-[0-9a-f]{16}")) throw new SecurityException("That request is malformed.");
        if (!contextIsWhole()) throw new SecurityException("That request leaves out something a person needs to decide, so nothing was signed.");
    }
}
