package app.poolhouse.phone;

import org.json.JSONException;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

/**
 * What a decrypted push carries: the request's id, title, risk and end, and the whole challenge (which holds the text) or, for a
 * request too large for a push, only {@code more}, and the phone fetches the rest from the device when the person opens it.
 */
final class PushPayload {
    final String id;
    final String title;
    final String agent;
    final String risk;
    final long expiresMs;
    final ApprovalView view;

    private PushPayload(JSONObject raw) throws JSONException {
        if (raw.optInt("v", 0) != 1) throw new JSONException("This push is not one this app reads.");
        id = raw.getString("id");
        title = raw.getString("title");
        agent = raw.optString("agent", "");
        risk = raw.optString("risk", "sensitive");
        expiresMs = raw.getLong("expires_ms");
        if (!id.matches("ap-[0-9a-f]{16}") || title.length() > 160 || !(risk.equals("routine") || risk.equals("sensitive") || risk.equals("irreversible"))) {
            throw new JSONException("This push is malformed.");
        }
        if (raw.has("challenge")) {
            JSONObject challenge = raw.getJSONObject("challenge");
            view = new ApprovalView(new JSONObject().put("id", id).put("state", "pending").put("title", title).put("risk", risk).put("session", agent)
                    .put("text", challenge.getString("text")).put("expires_ms", expiresMs).put("challenge", challenge));
        } else {
            view = null;
        }
    }

    static PushPayload parse(byte[] plaintext) throws JSONException {
        return new PushPayload(new JSONObject(new String(plaintext, StandardCharsets.UTF_8)));
    }
}
