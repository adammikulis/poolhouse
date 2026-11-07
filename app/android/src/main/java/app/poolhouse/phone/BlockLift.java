package app.poolhouse.phone;

import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** A node-issued, one-use block lift, checked before the person is asked to sign it. */
final class BlockLift {
    private final JSONObject offer;
    final String nonce;
    final String who;

    BlockLift(JSONObject source, String device, String key, String selected, long until, long now) throws Exception {
        FoundingCharter.fields(source, "v", "op", "node", "nonce", "who", "until_ms", "expires_ms", "key", "authority");
        offer = new JSONObject(source.toString());
        FoundingCharter.number(offer, "v", 1, 1);
        FoundingCharter.choice(offer, "op", "block_lift");
        nonce = FoundingCharter.text(offer, "nonce");
        who = FoundingCharter.text(offer, "who");
        long end = FoundingCharter.number(offer, "until_ms", 1, Long.MAX_VALUE);
        long expires = FoundingCharter.number(offer, "expires_ms", 1, Long.MAX_VALUE);
        if (!nonce.matches("[0-9a-f]{32}") || !FoundingCharter.text(offer, "authority").matches("[0-9a-f]{64}") || !who.equals(selected) || !device.equals(FoundingCharter.text(offer, "node"))
                || !key.equals(FoundingCharter.text(offer, "key")) || end != until || expires <= now || expires > end
                || expires - now > 120_000) throw new SecurityException("That block lift is no longer the one you chose.");
    }

    byte[] bytes() {
        return ("poolhouse/approval-block-lift/v1\n" + Canonical.of(offer)).getBytes(StandardCharsets.UTF_8);
    }
}
