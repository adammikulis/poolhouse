package app.poolhouse.phone;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The plain-words text of a request cut into the parts a screen lays out: what looks off first, the pool line, the risk, who
 * asks, what is asked and how far it reaches, what it cannot do, why, what a deny does and when it ends. The node wrote the
 * text and the key signs it; this only reads it, and a line it does not know is kept, never dropped.
 */
final class ApprovalText {
    private static final Pattern LINE = Pattern.compile("^(FLAG|POOL|RISK|WHO|ASK|SCOPE|CANNOT|WHY|DENY|EXPIRES)(?: (\\d+))?: (.*)$");
    final List<String> flags = new ArrayList<>();
    final List<String> asks = new ArrayList<>();
    final List<String> scopes = new ArrayList<>();
    final List<String> other = new ArrayList<>();
    String pool = "";
    String risk = "";
    String who = "";
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
                case "WHO": out.who = value; break;
                case "ASK": out.asks.add(value); break;
                case "SCOPE": out.scopes.add(value); break;
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

    /** The risk level in lower case: routine, sensitive or irreversible; empty when the text names none. */
    String level() {
        int dot = risk.indexOf('.');
        return (dot < 0 ? risk : risk.substring(0, dot)).toLowerCase(java.util.Locale.ROOT);
    }
}
