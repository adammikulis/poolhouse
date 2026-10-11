package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * The phone's side of the node's phone endpoint: one JSON request, one JSON answer, the device found at the first address of
 * the code that answers. A refusal is a {@link NodeError} whose message is one sentence for the person.
 */
final class NodeApi {
    private static final String BASE = "/phone/v1/";
    private static final int MOST_ANSWER = 1 << 20;
    private final Transport transport;
    private final String pin;
    private final List<String> endpoints;

    NodeApi(Transport transport, String pin, List<String> endpoints) {
        this.transport = transport;
        this.pin = pin;
        this.endpoints = new ArrayList<>(endpoints);
    }

    private JSONObject send(String method, String op, String token, JSONObject body) throws Exception {
        byte[] payload = method.equals("GET") ? new byte[0] : body.toString().getBytes(StandardCharsets.UTF_8);
        Exception last = null;
        for (String endpoint : new ArrayList<>(endpoints)) {
            byte[] raw;
            try {
                raw = transport.request(endpoint, pin, method, BASE + op, token, payload);
            } catch (SecurityException refused) {
                throw refused;
            } catch (Exception unreachable) {
                last = unreachable;
                continue;
            }
            if (raw.length > MOST_ANSWER) throw new IllegalArgumentException("The device answered with too much.");
            endpoints.remove(endpoint);
            endpoints.add(0, endpoint);
            JSONObject answer = new JSONObject(new String(raw, StandardCharsets.UTF_8));
            if (answer.optBoolean("ok")) return answer.getJSONObject("result");
            JSONObject error = answer.optJSONObject("error");
            throw new NodeError(error == null ? "invalid" : error.optString("code", "invalid"), error == null ? "The device refused the request." : error.optString("message", "The device refused the request."));
        }
        throw last == null ? new IllegalStateException("No address to try.") : last;
    }

    JSONObject hello() throws Exception {
        return send("GET", "hello", "", new JSONObject());
    }

    JSONObject add(JSONObject body) throws Exception {
        return send("POST", "add", "", body);
    }

    JSONObject status(String token) throws Exception {
        return send("POST", "status", token, new JSONObject());
    }

    JSONObject list(String token) throws Exception {
        return send("POST", "list", token, new JSONObject());
    }

    JSONObject get(String token, String id) throws Exception {
        return send("POST", "get", token, new JSONObject().put("id", id));
    }

    /** Answers when the requests change after {@code version}, or when {@code timeoutMs} (at most 15 s) has passed. */
    JSONObject waitFor(String token, long version, long timeoutMs) throws Exception {
        return send("POST", "wait", token, new JSONObject().put("after", version).put("timeout_ms", timeoutMs));
    }

    JSONObject decide(JSONObject body) throws Exception {
        return send("POST", "decide", "", body);
    }

    JSONObject setPushAddress(String token, String endpoint) throws Exception {
        return send("POST", "push", token, new JSONObject().put("endpoint", endpoint));
    }

    List<JSONObject> phones(String token) throws Exception {
        JSONArray held = send("POST", "phones", token, new JSONObject()).getJSONArray("phones");
        List<JSONObject> out = new ArrayList<>();
        for (int i = 0; i < held.length(); i++) out.add(held.getJSONObject(i));
        return out;
    }

    /** Ask for a phone to be removed: the request a key of a phone that stays then signs. */
    JSONObject removePhone(String token, String phoneId) throws Exception {
        return send("POST", "remove", token, new JSONObject().put("id", phoneId));
    }

    /** A link that adds one more phone; it is added only when a phone already added approves it. */
    String offer(String token) throws Exception {
        return send("POST", "offer", token, new JSONObject()).getString("link");
    }

    /** Whether an exception is the node saying this phone's token is not (or no longer) known. */
    static boolean removed(Exception failure) {
        return failure instanceof SecurityException;
    }

}
