package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.math.BigInteger;

/** A founding charter and the bytes a listed key accepts. */
final class FoundingCharter {
    private final JSONObject charter;
    private final JSONObject selected;
    private final byte[] signing;
    final String anchor;
    final String poolId;
    final String certificateWords;
    final String signerFingerprint;
    final String signerAlgorithm;
    final String signerRole;
    final String card;

    FoundingCharter(JSONObject source, String publicKey, String suppliedAnchor, String suppliedPool, byte[] suppliedBytes) throws Exception {
        Canonical.of(source);
        charter = new JSONObject(source.toString());
        validate(charter);
        JSONArray admins = charter.getJSONArray("admins");
        JSONObject found = null;
        JSONObject bare = new JSONObject(charter.toString());
        for (int i = 0; i < admins.length(); i++) {
            JSONObject admin = admins.getJSONObject(i);
            if (text(admin, "key").equals(publicKey)) found = admin;
            bare.getJSONArray("admins").getJSONObject(i).put("acceptance", "");
        }
        if (found == null) throw bad();
        selected = found;
        anchor = Hex.sha256(Canonical.of(bare));
        poolId = anchor.substring(0, 32);
        if (!anchor.equals(suppliedAnchor) || !poolId.equals(suppliedPool)) throw bad();
        JSONObject acceptance = new JSONObject().put("v", 3).put("anchor", anchor).put("prev", "");
        for (String key : new String[]{"key", "alg", "role", "claim", "person", "since"}) acceptance.put(key, selected.get(key));
        signing = ("poolhouse/pool-accept/v3\n" + Canonical.of(acceptance)).getBytes(StandardCharsets.UTF_8);
        if (!Arrays.equals(signing, suppliedBytes)) throw bad();
        certificateWords = Hex.words(Hex.sha256(Hex.parse(text(charter.getJSONObject("first"), "cert"))));
        signerFingerprint = Hex.sha256(Hex.parse(text(selected, "key")));
        signerAlgorithm = text(selected, "alg");
        signerRole = text(selected, "role") + "; " + claimDescription(selected);
        card = FoundingCard.of(this);
    }

    byte[] bytes() { return signing.clone(); }

    JSONObject terms() throws Exception { return new JSONObject(charter.toString()); }

    private static void validate(JSONObject c) throws Exception {
        fields(c, "v", "epoch", "name", "policy", "mode", "project", "first", "admins", "thresholds", "holds", "floors", "recover_delay_days", "succession", "from", "at_ms");
        number(c, "v", 4, 4);
        number(c, "epoch", 1, 1);
        choice(c, "mode", "admins");
        choice(c, "policy", "secure", "open");
        if (c.get("succession") != JSONObject.NULL || c.get("from") != JSONObject.NULL) throw bad();
        number(c, "at_ms", 1, Long.MAX_VALUE);
        String name = plain(c, "name", 40, false);
        if (!name.matches("[A-Za-z0-9][A-Za-z0-9 .-]*")) throw bad();
        if (!text(c, "project").matches("|[0-9a-fA-F]{16}")) throw bad();
        JSONObject first = c.getJSONObject("first");
        fields(first, "name", "cert", "delegate");
        if (!(first.get("delegate") instanceof Boolean)) throw bad();
        plain(first, "name", 40, false);
        certificateWords(text(first, "cert"));
        JSONArray admins = c.getJSONArray("admins");
        if (admins.length() < 1 || admins.length() > 16) throw bad();
        String previous = "";
        int recovery = 0, adminCount = 0;
        for (int i = 0; i < admins.length(); i++) {
            JSONObject k = admins.getJSONObject(i);
            fields(k, "key", "alg", "role", "claim", "person", "since", "acceptance", "label");
            String key = text(k, "key");
            if (previous.compareTo(key) >= 0) throw bad();
            previous = key;
            choice(k, "alg", "ed25519", "p256");
            publicKey(key, text(k, "alg"));
            choice(k, "role", "admin", "recovery");
            level(k);
            String person = plain(k, "person", 64, false);
            if (!person.matches("[A-Za-z0-9_:.-]+")) throw bad();
            plain(k, "label", 40, true);
            number(k, "since", 1, 1);
            if (!text(k, "acceptance").matches("(?:[0-9a-f]{2})*")) throw bad();
            if (text(k, "role").equals("recovery")) recovery++;
            else adminCount++;
        }
        if (recovery > 1 || adminCount == 0) throw bad();
        JSONObject t = c.getJSONObject("thresholds");
        fields(t, "routine", "sensitive", "unit");
        long routine = number(t, "routine", 1, 16), sensitive = number(t, "sensitive", 0, 16);
        choice(t, "unit", "person", "key");
        JSONObject floors = c.getJSONObject("floors");
        fields(floors, "routine", "sensitive", "governance");
        boolean hasComputer = false, typed = false;
        for (int i = 0; i < admins.length(); i++) {
            JSONObject k = admins.getJSONObject(i);
            if (text(k, "role").equals("admin") && level(k) >= 1) hasComputer = true;
        }
        for (String kind : new String[]{"routine", "sensitive", "governance"}) {
            int floor = floor(floors, kind);
            typed |= floor == 0;
            Set<String> people = new HashSet<>();
            int keys = 0;
            for (int i = 0; i < admins.length(); i++) {
                JSONObject k = admins.getJSONObject(i);
                if (text(k, "role").equals("admin") && level(k) >= floor) { keys++; people.add(text(k, "person")); }
            }
            int units = text(t, "unit").equals("person") ? people.size() : keys;
            long needed = kind.equals("routine") ? routine : sensitive == 0 ? Math.max(1, Math.min(2, people.size())) : sensitive;
            if (units < needed) throw bad();
        }
        if (typed && (hasComputer || first.getBoolean("delegate"))) throw bad();
        JSONObject h = c.getJSONObject("holds");
        fields(h, "admin_add_h", "admin_remove_h", "handoff_min", "dispute_d");
        number(h, "admin_add_h", 1, 720);
        number(h, "admin_remove_h", 1, 720);
        number(h, "handoff_min", 1, 1440);
        number(h, "dispute_d", 1, 30);
        number(c, "recover_delay_days", 1, 30);
    }

    static int floor(JSONObject floors, String kind) throws Exception {
        String value = text(floors, kind);
        int index = Arrays.asList("weak", "computer", "native", "phone").indexOf(value);
        if (index < 0) throw bad();
        return index;
    }

    /** Count only what the pool can verify: no claim exceeds computer assurance. */
    static int level(JSONObject key) throws Exception {
        String role = text(key, "role");
        Object value = key.get("claim");
        if (role.equals("recovery")) {
            if (value != JSONObject.NULL) throw bad();
            return 0;
        }
        if (!(value instanceof JSONObject)) throw bad();
        JSONObject claim = (JSONObject) value;
        fields(claim, "method", "os", "prompt");
        choice(claim, "os", "android", "macos", "linux", "windows", "other");
        String method = text(claim, "method"), prompt = text(claim, "prompt");
        switch (method) {
            case "phone-keystore": if (!prompt.equals("biometric")) throw bad(); return 1;
            case "helper": case "keystore-prompt": if (!prompt.equals("os-dialog") && !prompt.equals("biometric")) throw bad(); return 1;
            case "keystore-typed": if (!prompt.equals("typed-yes")) throw bad(); return 0;
            case "file": if (!prompt.equals("typed-yes") && !prompt.equals("none")) throw bad(); return 0;
            default: throw bad();
        }
    }

    static String claimDescription(JSONObject key) throws Exception {
        if (key.get("claim") == JSONObject.NULL) return "recovery key; counts for no threshold";
        JSONObject claim = key.getJSONObject("claim");
        return "claimed " + text(claim, "method") + " on " + text(claim, "os") + " with " + text(claim, "prompt")
                + "; counted as " + (level(key) == 0 ? "weak" : "computer") + "; the pool cannot verify the claim";
    }

    static String certificateWords(String cert) throws Exception {
        if (!cert.matches("(?:[0-9a-f]{2})+") || cert.length() > 32768) throw bad();
        byte[] der = Hex.parse(cert);
        ByteArrayInputStream input = new ByteArrayInputStream(der);
        X509Certificate certificate = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(input);
        if (input.available() != 0 || !Arrays.equals(der, certificate.getEncoded())) throw bad();
        byte[] spki = certificate.getPublicKey().getEncoded();
        byte[] prefix = Hex.parse("302a300506032b6570032100");
        if (spki.length != 44 || !Arrays.equals(prefix, Arrays.copyOf(spki, prefix.length))) throw bad();
        return Hex.words(Hex.sha256(der));
    }

    static void publicKey(String key, String alg) {
        if (alg.equals("ed25519")) {
            if (!key.matches("[0-9a-f]{64}")) throw bad();
            byte[] raw = Hex.parse(key);
            raw[31] &= 127;
            for (int i = 0; i < 16; i++) { byte b = raw[i]; raw[i] = raw[31-i]; raw[31-i] = b; }
            BigInteger p = BigInteger.ONE.shiftLeft(255).subtract(BigInteger.valueOf(19));
            BigInteger y = new BigInteger(1, raw);
            BigInteger d = BigInteger.valueOf(-121665).multiply(BigInteger.valueOf(121666).modInverse(p)).mod(p);
            BigInteger yy = y.multiply(y).mod(p);
            BigInteger denominator = d.multiply(yy).add(BigInteger.ONE).mod(p);
            if (denominator.signum() == 0) throw bad();
            BigInteger xx = yy.subtract(BigInteger.ONE).multiply(denominator.modInverse(p)).mod(p);
            if (xx.signum() != 0 && !xx.modPow(p.subtract(BigInteger.ONE).shiftRight(1), p).equals(BigInteger.ONE)) throw bad();
        } else {
            if (!key.matches("04[0-9a-f]{128}")) throw bad();
            BigInteger p = new BigInteger("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16);
            BigInteger b = new BigInteger("5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 16);
            BigInteger x = new BigInteger(key.substring(2,66),16), y = new BigInteger(key.substring(66),16);
            if (x.compareTo(p) >= 0 || y.compareTo(p) >= 0 || !y.multiply(y).mod(p).equals(x.pow(3).subtract(x.multiply(BigInteger.valueOf(3))).add(b).mod(p))) throw bad();
        }
    }

    static void fields(JSONObject o, String... names) {
        Set<String> expected = new HashSet<>(Arrays.asList(names));
        o.keys().forEachRemaining(key -> { if (!expected.remove(key)) throw bad(); });
        if (!expected.isEmpty()) throw bad();
    }

    static String text(JSONObject o, String key) throws org.json.JSONException {
        Object value = o.get(key);
        if (!(value instanceof String)) throw bad();
        return (String) value;
    }

    static String plain(JSONObject o, String key, int most, boolean empty) throws org.json.JSONException {
        String value = text(o, key);
        if ((!empty && value.isEmpty()) || value.length() > most || value.chars().anyMatch(ch -> ch < 32 || ch == 127)) throw bad();
        return value;
    }

    static void choice(JSONObject o, String key, String... values) throws org.json.JSONException {
        if (!Arrays.asList(values).contains(text(o, key))) throw bad();
    }

    static long number(JSONObject o, String key, long min, long max) throws org.json.JSONException {
        Object value = o.get(key);
        if (!(value instanceof Integer) && !(value instanceof Long)) throw bad();
        long n = ((Number) value).longValue();
        if (n < min || n > max) throw bad();
        return n;
    }

    private static SecurityException bad() {
        return new SecurityException("That founding request does not match its charter.");
    }
}
