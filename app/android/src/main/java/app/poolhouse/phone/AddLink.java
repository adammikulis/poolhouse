package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * The code a person scans to add this phone: where the device is, the one certificate to trust, a one-time secret and when it
 * ends. Anything else in it, or anything out of shape, is refused before the phone says a word to the network.
 */
final class AddLink {
    static final String PREFIX = "poolhouse://add-phone?data=";
    /** How far the phone's clock may differ from the computer's before the code reads as ended: the node decides the real end. */
    static final long CLOCK_SLACK_S = 300;
    /** The largest epoch a JSON number carries without losing a digit. */
    private static final long MOST_EPOCH = 9007199254740991L;
    final String pin;
    final List<String> endpoints;
    final String secret;
    final long expires;
    final String name;
    final String policy;
    /** What the code says this phone would be listed under in a pool; null when the code carries no terms (the device is in no pool). */
    final Admit admit;

    /** The terms a code carries for a phone that is to be listed as an admin key: the pool, the charter its listing follows, the epoch it joins at and its person. */
    static final class Admit {
        final String pool;
        final String prev;
        final long since;
        final String person;

        private Admit(JSONObject terms) throws Exception {
            Set<String> fields = new HashSet<>();
            terms.keys().forEachRemaining(fields::add);
            if (!fields.equals(Set.of("pool", "prev", "since", "person"))) throw malformed();
            Object when = terms.get("since");
            if (!(when instanceof Integer || when instanceof Long) || !(terms.get("pool") instanceof String) || !(terms.get("prev") instanceof String)
                    || !(terms.get("person") instanceof String)) {
                throw malformed();
            }
            pool = terms.getString("pool");
            prev = terms.getString("prev");
            person = terms.getString("person");
            since = ((Number) when).longValue();
            if (!pool.matches("[a-f0-9]{32}") || !prev.matches("[a-f0-9]{64}") || !person.matches("[A-Za-z0-9_:.-]{1,64}") || since < 2 || since > MOST_EPOCH) throw malformed();
        }
    }

    private AddLink(JSONObject data, long now) throws Exception {
        Set<String> fields = new HashSet<>();
        data.keys().forEachRemaining(fields::add);
        Set<String> expected = new HashSet<>(Set.of("v", "pin", "endpoints", "secret", "expires", "name", "policy"));
        if (fields.contains("admit")) expected.add("admit");
        if (!fields.equals(expected) || data.optInt("v", 0) != 1) {
            throw new IllegalArgumentException("This is not a code that adds a phone.");
        }
        admit = fields.contains("admit") ? new Admit(data.getJSONObject("admit")) : null;
        pin = data.getString("pin");
        secret = data.getString("secret");
        name = data.getString("name");
        policy = data.getString("policy");
        expires = data.getLong("expires");
        if (!pin.matches("[a-f0-9]{64}") || !secret.matches("[A-Za-z0-9_-]{43}") || !(policy.equals("open") || policy.equals("secure"))
                || name.length() > 40 || !name.matches("[\\x20-\\x7e]*")) {
            throw new IllegalArgumentException("The code is malformed.");
        }
        if (expires + CLOCK_SLACK_S < now || expires > now + CLOCK_SLACK_S + 600) throw new IllegalArgumentException("The code has expired; ask for a new one.");
        JSONArray listed = data.getJSONArray("endpoints");
        if (listed.length() < 1 || listed.length() > 4) throw new IllegalArgumentException("The code names no address, or too many.");
        List<String> found = new ArrayList<>();
        for (int i = 0; i < listed.length(); i++) {
            String endpoint = listed.getString(i);
            URI host = new URI(endpoint);
            if (!"https".equals(host.getScheme()) || host.getHost() == null || host.getUserInfo() != null || host.getQuery() != null
                    || host.getFragment() != null || !(host.getRawPath().isEmpty() || host.getRawPath().equals("/"))
                    || host.getPort() < 1 || host.getPort() > 65535 || endpoint.length() > 2048) {
                throw new IllegalArgumentException("The code must name HTTPS addresses of the device.");
            }
            found.add(endpoint);
        }
        endpoints = Collections.unmodifiableList(found);
    }

    private static IllegalArgumentException malformed() {
        return new IllegalArgumentException("The code is malformed.");
    }

    static AddLink parse(String text, long now) throws Exception {
        if (text == null || text.length() > 4096 || !text.startsWith(PREFIX)) throw new IllegalArgumentException("Scan the code the computer shows to add a phone.");
        String encoded = URLDecoder.decode(text.substring(PREFIX.length()).trim(), "UTF-8");
        if (!encoded.matches("[A-Za-z0-9_-]+")) throw new IllegalArgumentException("The code is malformed.");
        byte[] raw = Base64.getUrlDecoder().decode(encoded);
        if (raw.length > 3072 || !Base64.getUrlEncoder().withoutPadding().encodeToString(raw).equals(encoded)) throw new IllegalArgumentException("The code is malformed.");
        return new AddLink(new JSONObject(StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(raw)).toString()), now);
    }
}
