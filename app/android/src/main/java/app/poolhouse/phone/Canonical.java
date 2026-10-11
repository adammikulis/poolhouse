package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * JSON as the node writes it before it is signed: keys sorted, no spaces, whole numbers only and plain ASCII text,
 * escaped the way the node escapes it. A value the node would not write the same way is refused.
 */
final class Canonical {
    private Canonical() { }

    static String of(Object value) {
        StringBuilder out = new StringBuilder();
        write(out, value);
        return out.toString();
    }

    private static void write(StringBuilder out, Object value) {
        if (value == null || value == JSONObject.NULL) out.append("null");
        else if (value instanceof JSONObject) {
            JSONObject object = (JSONObject) value;
            List<String> keys = new ArrayList<>();
            object.keys().forEachRemaining(keys::add);
            Collections.sort(keys);
            out.append('{');
            for (int i = 0; i < keys.size(); i++) {
                if (i > 0) out.append(',');
                text(out, keys.get(i));
                out.append(':');
                write(out, object.opt(keys.get(i)));
            }
            out.append('}');
        } else if (value instanceof JSONArray) {
            JSONArray array = (JSONArray) value;
            out.append('[');
            for (int i = 0; i < array.length(); i++) {
                if (i > 0) out.append(',');
                write(out, array.opt(i));
            }
            out.append(']');
        } else if (value instanceof String) text(out, (String) value);
        else if (value instanceof Boolean) out.append(value);
        else if (value instanceof Integer || value instanceof Long) out.append(value);
        else throw new IllegalArgumentException("Only whole numbers, text, lists and objects are signed.");
    }

    private static void text(StringBuilder out, String value) {
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"') out.append("\\\"");
            else if (c == '\\') out.append("\\\\");
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c == '\b') out.append("\\b");
            else if (c == '\f') out.append("\\f");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else if (c > 0x7e) throw new IllegalArgumentException("Signed text is plain ASCII.");
            else out.append(c);
        }
        out.append('"');
    }
}
