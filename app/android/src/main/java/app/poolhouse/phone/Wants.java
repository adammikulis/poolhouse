package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;

/**
 * What a request wants, read by the phone from the structured asks the key signs and not from the node's prose: one terse line for
 * each grant (the action, where, how often and for how long). The node's text says the same things in sentences; this is the check
 * that the sentences and the bytes agree, because a grant is built from the bytes.
 */
final class Wants {
    private Wants() { }

    /** A span in words: {@code 10 minutes}, {@code 1 hour}, {@code 2 days}. */
    static String span(long ms) {
        long[] sizes = {86_400_000L, 3_600_000L, 60_000L, 1000L};
        String[] units = {"day", "hour", "minute", "second"};
        for (int i = 0; i < sizes.length; i++) {
            if (ms >= sizes[i] && ms % sizes[i] == 0) {
                long n = ms / sizes[i];
                return n + " " + units[i] + (n == 1 ? "" : "s");
            }
        }
        return ms + " milliseconds";
    }

    private static String where(String resource) {
        if (resource.equals("*")) return "anything";
        if (resource.endsWith("*")) return "anything starting with " + resource.substring(0, resource.length() - 1);
        return resource;
    }

    /** The lines for the grants of {@code challenge}; empty for a request that asks for none (a phone's own, a notice). */
    static List<String> of(Challenge challenge) {
        List<String> out = new ArrayList<>();
        JSONObject action = challenge.action();
        JSONArray grants = action == null ? null : action.optJSONArray("grants");
        for (int i = 0; grants != null && i < grants.length(); i++) {
            JSONObject grant = grants.optJSONObject(i);
            if (grant == null) continue;
            String uses = grant.has("uses") ? (grant.optLong("uses") == 1 ? "once" : grant.optLong("uses") + " times") : "as often as it is needed";
            out.add(grant.optString("action") + " on " + where(grant.optString("resource")) + ", " + uses + ", for " + span(grant.optLong("lasts_ms"))
                    + (grant.optBoolean("review") ? ", each use reviewed" : ""));
        }
        return out;
    }
}
