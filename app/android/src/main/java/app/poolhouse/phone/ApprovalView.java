package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;

/** One request as the node shows it to a phone: its state, the text, and the challenge a key signs. */
final class ApprovalView {
    final JSONObject raw;
    final String id;
    final String state;
    final String title;
    /** The level the signed text names (routine, sensitive or irreversible): never the unsigned field beside it. */
    final String risk;
    final String session;
    final String text;
    final long expiresMs;
    final Challenge challenge;

    ApprovalView(JSONObject raw) throws JSONException {
        this.raw = raw;
        id = raw.getString("id");
        state = raw.getString("state");
        title = raw.optString("title", "");
        session = raw.optString("session", "");
        text = raw.getString("text");
        String level = ApprovalText.parse(text).level();
        risk = level.isEmpty() ? "sensitive" : level;
        expiresMs = raw.getLong("expires_ms");
        challenge = new Challenge(raw.getJSONObject("challenge"));
        if (!id.matches("ap-[0-9a-f]{16}") || !challenge.id().equals(id) || !challenge.text().equals(text)) throw new JSONException("The request is not what it says it is.");
    }

    boolean pending() {
        return "pending".equals(state);
    }

    ApprovalText parts() {
        return ApprovalText.parse(text);
    }

    /** Whether Approve opens the whole request before anything is signed: not routine, or flagged by the node. */
    boolean opensScreen() {
        return ApprovalFlow.opensScreen(risk, !parts().flags.isEmpty());
    }

    static List<ApprovalView> all(JSONArray list) throws JSONException {
        List<ApprovalView> out = new ArrayList<>();
        for (int i = 0; i < list.length(); i++) out.add(new ApprovalView(list.getJSONObject(i)));
        return out;
    }
}
