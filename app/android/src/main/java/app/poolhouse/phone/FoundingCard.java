package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;

/** The founding terms read from the charter the key accepts. */
final class FoundingCard {
    private FoundingCard() { }

    static String of(FoundingCharter verified) throws Exception {
        JSONObject c = verified.terms();
        JSONObject first = c.getJSONObject("first"), t = c.getJSONObject("thresholds"), h = c.getJSONObject("holds");
        StringBuilder out = new StringBuilder("Make pool ").append(quote(c.getString("name")))
            .append("\nPool ID: ").append(verified.poolId)
            .append("\nFounding anchor: ").append(verified.anchor)
            .append("\nProject: ").append(c.getString("project").isEmpty() ? "none" : c.getString("project"))
            .append("\nSelected signer: ").append(verified.signerFingerprint)
            .append("; ").append(verified.signerAlgorithm).append("; ").append(verified.signerRole)
            .append("\nJoin policy: ").append(c.getString("policy"))
            .append("\nFirst device: ").append(quote(first.getString("name")))
            .append("\nFirst device may admit without asking: ").append(first.getBoolean("delegate") ? "yes, for 30 days" : "no")
            .append("\nCertificate check words: ").append(verified.certificateWords);
        JSONArray admins = c.getJSONArray("admins");
        for (int i = 0; i < admins.length(); i++) {
            JSONObject k = admins.getJSONObject(i);
            out.append("\n").append(k.getString("role")).append(": ").append(quote(k.getString("label")))
                .append("; person ").append(quote(k.getString("person"))).append("; ").append(k.getString("alg"))
                .append("; ").append(FoundingCharter.claimDescription(k)).append("; key ").append(Hex.words(Hex.sha256(Hex.parse(k.getString("key")))));
        }
        out.append("\nRoutine threshold: ").append(t.getLong("routine"))
            .append("\nSensitive threshold: ").append(sensitive(c))
            .append("\nCount by: ").append(t.getString("unit"))
            .append("\nRoutine approval: ").append(floorWords(c.getJSONObject("floors").getString("routine")))
            .append("\nSensitive approval: ").append(floorWords(c.getJSONObject("floors").getString("sensitive")))
            .append("\nGovernance approval: ").append(floorWords(c.getJSONObject("floors").getString("governance")))
            .append("\nAdd admin hold: ").append(h.getLong("admin_add_h")).append(" hours")
            .append("\nRemove admin hold: ").append(h.getLong("admin_remove_h")).append(" hours")
            .append("\nHandoff hold: ").append(h.getLong("handoff_min")).append(" minutes")
            .append("\nDispute hold: ").append(h.getLong("dispute_d")).append(" days")
            .append("\nRecovery delay: ").append(c.getLong("recover_delay_days")).append(" days");
        return out.toString();
    }

    private static String floorWords(String floor) {
        switch (floor) {
            case "weak": return "a typed yes; a program running as you could approve it";
            case "computer": return "this computer; a program running as you could approve it without asking";
            case "native": return "this computer, protected";
            case "phone": return "your phone";
            default: throw new IllegalArgumentException("Unknown approval floor");
        }
    }

    private static String sensitive(JSONObject c) throws Exception {
        long threshold = c.getJSONObject("thresholds").getLong("sensitive");
        if (threshold != 0) return Long.toString(threshold);
        java.util.Set<String> people = new java.util.HashSet<>();
        JSONArray admins = c.getJSONArray("admins");
        for (int i = 0; i < admins.length(); i++) {
            JSONObject k = admins.getJSONObject(i);
            if ("admin".equals(k.getString("role")) && FoundingCharter.level(k) >= FoundingCharter.floor(c.getJSONObject("floors"), "sensitive")) people.add(k.getString("person"));
        }
        return Math.max(1, Math.min(2, people.size())) + " (automatic: two once two distinct people hold qualifying admin keys)";
    }

    private static String quote(String value) { return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""; }
}
