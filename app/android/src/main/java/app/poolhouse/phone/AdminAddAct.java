package app.poolhouse.phone;

import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.Signature;
import java.util.Arrays;

/**
 * The act that lists a new phone as an admin key, as the request to add that phone carries it, checked before the person's
 * fingerprint is asked for the second signature that lists it. The act is the one shape the device writes for it: the phone being added
 * is the key it lists, as a strong admin key of one person, joining at the next epoch, written lately and valid for at most a week, with the
 * key's own acceptance of exactly these terms. Anything else is refused, and nothing is signed.
 */
final class AdminAddAct {
    static final String DOMAIN = "poolhouse/pool-act/v2\n";
    /** How far the act's time may be ahead of the device's clock. */
    static final long SKEW_MS = 300_000L;
    /** The longest an act may stay valid, from its writing. */
    static final long WINDOW_MS = 604_800_000L;
    static final int MOST_BYTES = 16384;
    private static final long MOST = 9007199254740991L;
    private final byte[] signing;
    /** Two lines for the screen: who the phone is listed for and the words of its key. */
    final String card;
    final String person;
    /** The SHA-256 of the canonical act without its signatures. */
    final String id;

    /** The act {@code challenge} carries, or null when it lists no phone; refused when it lists one in any way but the one shape. */
    static AdminAddAct of(Challenge challenge, long nowMs) {
        JSONObject action = challenge.action();
        if (action == null || !"phone_add".equals(action.optString("kind")) || !action.has("act")) return null;
        try {
            return new AdminAddAct(action, nowMs);
        } catch (Exception unreadable) {
            throw new SecurityException("The listing of this phone does not match what the device wrote for it, so nothing was signed.");
        }
    }

    private AdminAddAct(JSONObject source, long nowMs) throws Exception {
        Canonical.of(source);
        JSONObject action = new JSONObject(source.toString());
        FoundingCharter.fields(action, "kind", "phone", "name", "key", "fingerprint", "act");
        FoundingCharter.plain(action, "phone", 64, false);
        String name = FoundingCharter.plain(action, "name", 40, false);
        String key = FoundingCharter.text(action, "key");
        FoundingCharter.publicKey(key, "p256");
        if (!Hex.sha256(Hex.parse(key)).equals(FoundingCharter.text(action, "fingerprint")) || nowMs <= 0) throw new IllegalArgumentException();
        JSONObject act = action.getJSONObject("act");
        FoundingCharter.fields(act, "v", "pool", "epoch", "kind", "body", "at_ms", "not_after", "nonce", "prev", "signatures");
        FoundingCharter.number(act, "v", 2, 2);
        FoundingCharter.choice(act, "kind", "admin_add");
        String pool = FoundingCharter.text(act, "pool"), prev = FoundingCharter.text(act, "prev"), nonce = FoundingCharter.text(act, "nonce");
        if (!pool.matches("[0-9a-f]{32}") || !prev.matches("[0-9a-f]{64}") || !nonce.matches("[0-9a-f]{32}")) throw new IllegalArgumentException();
        long epoch = FoundingCharter.number(act, "epoch", 1, MOST);
        long at = FoundingCharter.number(act, "at_ms", 1, nowMs + SKEW_MS);
        long until = FoundingCharter.number(act, "not_after", 1, Long.MAX_VALUE);
        if (until <= nowMs || until <= at || until - at > WINDOW_MS) throw new IllegalArgumentException();
        if (act.getJSONArray("signatures").length() != 0 || Canonical.of(act).getBytes(StandardCharsets.UTF_8).length > MOST_BYTES) throw new IllegalArgumentException();
        JSONObject body = act.getJSONObject("body");
        FoundingCharter.fields(body, "entry");
        JSONObject entry = body.getJSONObject("entry");
        FoundingCharter.fields(entry, "key", "alg", "role", "strength", "person", "since", "acceptance", "label");
        FoundingCharter.choice(entry, "alg", "p256");
        FoundingCharter.choice(entry, "role", "admin");
        FoundingCharter.choice(entry, "strength", "strong");
        person = FoundingCharter.text(entry, "person");
        long since = FoundingCharter.number(entry, "since", 2, MOST);
        String acceptance = FoundingCharter.text(entry, "acceptance"), label = FoundingCharter.plain(entry, "label", 40, false);
        if (!person.matches("[A-Za-z0-9_:.-]{1,64}") || since != epoch + 1 || !key.equals(FoundingCharter.text(entry, "key")) || !name.equals(label)) throw new IllegalArgumentException();
        accepted(key, acceptance, AddRequest.acceptanceBytes(key, person, since, pool, prev));
        JSONObject unsigned = new JSONObject(act.toString());
        unsigned.remove("signatures");
        String canonical = Canonical.of(unsigned);
        signing = (DOMAIN + canonical).getBytes(StandardCharsets.UTF_8);
        JSONObject typed = new JSONObject().put("v", 2).put("pool", pool).put("epoch", epoch).put("kind", "admin_add").put("at_ms", at).put("not_after", until)
                .put("nonce", nonce).put("prev", prev).put("body", new JSONObject().put("entry", new JSONObject().put("key", key).put("alg", "p256").put("role", "admin")
                        .put("strength", "strong").put("person", person).put("since", since).put("acceptance", acceptance).put("label", label)));
        if (!Arrays.equals(signing, (DOMAIN + Canonical.of(typed)).getBytes(StandardCharsets.UTF_8))) throw new IllegalArgumentException();
        id = Hex.sha256(canonical);
        card = "Lists " + JSONObject.quote(name) + " as an admin key of " + person + ".\nIts key: " + Hex.words(Hex.sha256(Hex.parse(key))) + ", joining at epoch " + since + ".";
    }

    /** The bytes the approving key signs to list the phone: the act without its signatures, under the act domain. */
    byte[] bytes() { return signing.clone(); }

    /** Refuses unless {@code acceptance} is the key's own strict low-s signature over {@code message}. */
    private static void accepted(String key, String acceptance, byte[] message) throws Exception {
        if (!acceptance.matches("(?:[0-9a-f]{2}){8,72}")) throw new IllegalArgumentException();
        byte[] der = Hex.parse(acceptance);
        if (!Arrays.equals(P256Signature.normalize(der), der)) throw new IllegalArgumentException();
        Signature verifier = Signature.getInstance("SHA256withECDSA");
        verifier.initVerify(WebPushCipher.publicKey(Hex.parse(key)));
        verifier.update(message);
        if (!verifier.verify(der)) throw new IllegalArgumentException();
    }
}
