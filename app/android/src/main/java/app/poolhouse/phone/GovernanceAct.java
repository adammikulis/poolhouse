package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Proposed admission or removal bytes against independently pinned founding terms. */
final class GovernanceAct {
    private final byte[] signing;
    final String card;
    final String id;

    GovernanceAct(FoundingCharter founding, JSONObject source, String selectedKey, byte[] supplied, long nowMs) throws Exception {
        Canonical.of(source);
        JSONObject act = new JSONObject(source.toString()), terms = founding.terms();
        FoundingCharter.fields(act, "v", "pool", "epoch", "kind", "body", "at_ms", "not_after", "nonce", "prev", "signatures");
        FoundingCharter.number(act, "v", 2, 2);
        FoundingCharter.number(act, "epoch", 1, 1);
        FoundingCharter.choice(act, "kind", "admit", "remove");
        if (!founding.poolId.equals(FoundingCharter.text(act, "pool")) || !founding.anchor.equals(FoundingCharter.text(act, "prev"))) throw bad();
        if (!FoundingCharter.text(act, "nonce").matches("[0-9a-f]{32}") || nowMs <= 0) throw bad();
        long at = FoundingCharter.number(act, "at_ms", 1, nowMs);
        long until = FoundingCharter.number(act, "not_after", 1, Long.MAX_VALUE);
        Set<String> people = new HashSet<>();
        boolean eligible = false;
        String algorithm = "";
        JSONArray admins = terms.getJSONArray("admins");
        for (int i = 0; i < admins.length(); i++) {
            JSONObject key = admins.getJSONObject(i);
            if ("admin".equals(key.getString("role")) && "strong".equals(key.getString("strength"))) {
                people.add(key.getString("person"));
                if (selectedKey.equals(key.getString("key"))) { eligible = true; algorithm = key.getString("alg"); }
            }
        }
        long window = people.size() <= 1 ? 3600000 : 604800000;
        if (!eligible || until <= nowMs || until <= at || until - at > window || until - nowMs > window) throw bad();
        if (act.getJSONArray("signatures").length() != 0 || Canonical.of(act).getBytes(StandardCharsets.UTF_8).length > 16384) throw bad();
        JSONObject body = act.getJSONObject("body");
        String details = "admit".equals(act.getString("kind")) ? admission(body) : removal(body);
        act.remove("signatures");
        String canonical = Canonical.of(act);
        signing = ("poolhouse/pool-act/v2\n" + canonical).getBytes(StandardCharsets.UTF_8);
        if (!Arrays.equals(signing, supplied)) throw bad();
        id = Hex.sha256(canonical);
        card = "Proposed " + act.getString("kind") + " for pool: " + founding.poolId
                + "\nPolicy: " + terms.getString("policy") + "\nAdmin key: " + Hex.words(Hex.sha256(Hex.parse(selectedKey)))
                + "\nSigner: strong admin, " + algorithm + "\nFounding terms only; current governance is not verified.\nEpoch: 1\nWritten at: " + at + "\nExpires at: " + until + "\n" + details
                + "\nSigned body: " + Canonical.of(body);
    }

    byte[] bytes() { return signing.clone(); }

    private static String admission(JSONObject body) throws Exception {
        FoundingCharter.fields(body, "cert", "name", "how", "via");
        String name = FoundingCharter.plain(body, "name", 80, true);
        FoundingCharter.choice(body, "how", "join-code", "open", "tap", "delegate", "handoff");
        FoundingCharter.choice(body, "via", "", "segment");
        return "Device name: " + JSONObject.quote(name) + "\nCertificate: " + FoundingCharter.certificateWords(FoundingCharter.text(body, "cert"))
                + "\nJoin method: " + body.getString("how") + "\nChecked via: " + JSONObject.quote(body.getString("via"));
    }

    private static String removal(JSONObject body) throws Exception {
        FoundingCharter.fields(body, "fingerprint", "cut");
        String fingerprint = FoundingCharter.text(body, "fingerprint");
        if (!fingerprint.matches("[0-9a-f]{64}")) throw bad();
        JSONArray cut = body.getJSONArray("cut");
        if (cut.length() > 256) throw bad();
        StringBuilder logs = new StringBuilder();
        for (int i = 0; i < cut.length(); i++) {
            JSONObject entry = cut.getJSONObject(i);
            FoundingCharter.fields(entry, "board", "origin", "seq", "hash");
            String board = FoundingCharter.text(entry, "board"), origin = FoundingCharter.text(entry, "origin"), hash = FoundingCharter.text(entry, "hash");
            long seq = FoundingCharter.number(entry, "seq", 0, Long.MAX_VALUE);
            if (!board.matches("[a-z0-9][a-z0-9._-]{0,47}") || board.contains("..") || !origin.matches("[0-9a-f]{32}") || !(hash.matches("[0-9a-f]{64}") || seq == 0 && hash.isEmpty())) throw bad();
            logs.append("\nKeep log ").append(board).append(" from ").append(origin).append(" through entry ").append(seq).append(" at hash ").append(JSONObject.quote(hash));
        }
        return "Remove device: " + Hex.words(fingerprint) + "\nFingerprint: " + fingerprint
                + "\nIt stops working with every device. Only the listed log prefixes stand.\nCut hash: " + Hex.sha256(Canonical.of(cut)) + logs;
    }

    private static SecurityException bad() { return new SecurityException("That proposed act does not match its founding terms."); }
}
