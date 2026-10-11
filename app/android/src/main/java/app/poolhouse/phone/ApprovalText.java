package app.poolhouse.phone;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The plain-words text of a request cut into the parts a screen lays out: what looks off first, the pool line, the risk, what the
 * person said, who asks, how the node judged it against what was said, whether the session read content from outside, what is
 * asked and how far it reaches, an undo window, the number to match, what it cannot do, why, what a deny does and when it ends.
 * The node wrote the text and the key signs it; this only reads it, and a line it does not know is kept, never dropped.
 */
final class ApprovalText {
    private static final Pattern LINE = Pattern.compile("^(FLAG|POOL|RISK|SAID|WHO|VERDICT|TAINT|ASK|SCOPE|HELD|PICK|CANNOT|WHY|DENY|EXPIRES)(?: (\\d+))?: (.*)$");
    final List<String> flags = new ArrayList<>();
    final List<String> asks = new ArrayList<>();
    final List<String> scopes = new ArrayList<>();
    final List<String> other = new ArrayList<>();
    String pool = "";
    String risk = "";
    String said = "";
    String who = "";
    String verdict = "";
    String taint = "";
    String held = "";
    String pick = "";
    String cannot = "";
    String why = "";
    String deny = "";
    String expires = "";

    static ApprovalText parse(String text) {
        ApprovalText out = new ApprovalText();
        for (String line : text.split("\n")) {
            Matcher found = LINE.matcher(line);
            if (!found.matches()) { out.other.add(line); continue; }
            String value = found.group(3);
            switch (found.group(1)) {
                case "FLAG": out.flags.add(value); break;
                case "POOL": out.pool = value; break;
                case "RISK": out.risk = value; break;
                case "SAID": out.said = value; break;
                case "WHO": out.who = value; break;
                case "VERDICT": out.verdict = value; break;
                case "TAINT": out.taint = value; break;
                case "ASK": out.asks.add(value); break;
                case "SCOPE": out.scopes.add(value); break;
                case "HELD": out.held = value; break;
                case "PICK": out.pick = value; break;
                case "CANNOT": out.cannot = value; break;
                case "WHY": out.why = value; break;
                case "DENY": out.deny = value; break;
                default: out.expires = value;
            }
        }
        return out;
    }

    /** True when every part a person needs to decide is there. */
    boolean complete() {
        return !risk.isEmpty() && !who.isEmpty() && !asks.isEmpty() && asks.size() == scopes.size() && !cannot.isEmpty()
                && !why.isEmpty() && !deny.isEmpty() && !expires.isEmpty() && other.isEmpty();
    }

    /**
     * True when the text of a request of {@code kind} has every part its kind carries: a request an agent made also says what the person
     * said, how the node judged it and whether the session read content from outside; a notice or a phone's own request carries none of
     * those.
     */
    boolean completeFor(String kind) {
        return complete() && (!"grants".equals(kind) || (!said.isEmpty() && !verdict.isEmpty() && !taint.isEmpty()));
    }

    /** The risk level in lower case: routine, sensitive or irreversible; empty when the text names none. */
    String level() {
        int dot = risk.indexOf('.');
        return (dot < 0 ? risk : risk.substring(0, dot)).toLowerCase(java.util.Locale.ROOT);
    }
}
