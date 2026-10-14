package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.function.Consumer;

final class PhoneFoundingTest {
    private static JSONObject vector;
    private static int checks;
    interface Throwing { void run() throws Exception; }

    public static void main(String[] args) throws Exception {
        vector = new JSONObject(Files.readString(Path.of(args[0])));
        JSONObject source = charter();
        FoundingCharter parsed = read(source);
        equal(vector.getString("canonical"), Canonical.of(source));
        equal(vector.getString("anchor"), parsed.anchor);
        equal(vector.getString("pool_id"), parsed.poolId);
        equal(vector.getString("signing_bytes"), Hex.of(parsed.bytes()));
        JSONArray acceptances = vector.getJSONArray("acceptances");
        for (int i = 0; i < acceptances.length(); i++) {
            JSONObject a = acceptances.getJSONObject(i);
            FoundingCharter each = new FoundingCharter(source, a.getString("key"), parsed.anchor, parsed.poolId, Hex.parse(a.getString("bytes")));
            equal(a.getString("bytes"), Hex.of(each.bytes()));
        }
        String card = parsed.card;
        for (String shown : new String[]{"secure", "first device", "p256", "ed25519", "strong", "weak", "recovery", "48 hours", "72 hours", "30 minutes", "9 days", "14 days", "Count by: person"}) {
            if (!card.contains(shown)) throw new AssertionError("Missing card field: " + shown);
        }
        equal(Hex.words(Hex.sha256(Hex.parse(source.getJSONObject("first").getString("cert")))), parsed.certificateWords);
        source.put("name", "changed");
        byte[] returned = parsed.bytes(); returned[0] = 0;
        equal(card, parsed.card);
        equal(vector.getString("signing_bytes"), Hex.of(parsed.bytes()));
        changed(c -> c.put("name", "other"));
        changed(c -> c.put("policy", "open"));
        changed(c -> c.put("project", "fedcba9876543210"));
        changed(c -> c.put("at_ms", c.getLong("at_ms") + 1));
        changed(c -> c.getJSONObject("first").put("name", "other device"));
        changed(c -> c.getJSONObject("first").put("cert", c.getJSONObject("first").getString("cert").replaceFirst("30", "31")));
        changed(c -> c.getJSONObject("thresholds").put("routine", 2));
        changed(c -> c.getJSONObject("thresholds").put("sensitive", 0));
        changed(c -> c.getJSONObject("thresholds").put("unit", "key"));
        for (String key : new String[]{"admin_add_h", "admin_remove_h", "handoff_min", "dispute_d"}) changed(c -> c.getJSONObject("holds").put(key, 1));
        changed(c -> c.put("recover_delay_days", 1));
        for (String key : new String[]{"person", "label", "strength", "role", "alg", "key", "since"}) changed(c -> c.getJSONArray("admins").getJSONObject(0).put(key, "other"));
        for (Object version : new Object[]{2,4,"3",3.0,JSONObject.NULL}) changed(c -> c.put("v", version));
        for (String key : new String[]{"epoch", "at_ms", "recover_delay_days"}) changed(c -> c.put(key, "1"));
        changed(c -> c.put("epoch", 2));
        changed(c -> c.put("summary", "Make an open pool"));
        changed(c -> c.put("name", "caf\u00e9"));
        changed(c -> c.getJSONArray("admins").getJSONObject(0).put("label", "caf\u00e9"));
        changed(c -> c.put("\u00e9", "injected"));
        changed(c -> c.getJSONArray("admins").put(c.getJSONArray("admins").getJSONObject(0)));
        changed(c -> c.getJSONObject("first").put("name", "line\nbreak"));
        changed(c -> c.getJSONObject("holds").put("dispute_d", -1));
        refused(() -> new FoundingCharter(charter(), "00", vector.getString("anchor"), vector.getString("pool_id"), signing()));
        refused(() -> new FoundingCharter(charter(), selected(), "0".repeat(64), vector.getString("pool_id"), signing()));
        refused(() -> new FoundingCharter(charter(), selected(), vector.getString("anchor"), "0".repeat(32), signing()));
        byte[] wrong = signing(); wrong[0] ^= 1;
        refused(() -> new FoundingCharter(charter(), selected(), vector.getString("anchor"), vector.getString("pool_id"), wrong));
        refused(() -> new FoundingCharter(charter(), selected(), vector.getString("anchor"), vector.getString("pool_id"), Arrays.copyOf(signing(), signing().length + 1)));
        byte[] low = Hex.parse(vector.getString("low_signature"));
        equal(Hex.of(low), Hex.of(P256Signature.normalize(Hex.parse(vector.getString("high_signature")))));
        equal(Hex.of(low), Hex.of(P256Signature.normalize(low)));
        for (String malformed : new String[]{"", "3006020100020101", "3006020101020100", "308106020101020101", "300702020001020101", "3006020180020101", "300602010102010100", "3006020101020101ff", "3006020101020201", "3006020101030101", "3006020101020101ffff", "3026022100ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551020101"}) {
            refused(() -> P256Signature.normalize(Hex.parse(malformed)));
        }
        equal("3006020101020101", Hex.of(P256Signature.normalize(Hex.parse("3006020101020101"))));
        changed(c -> c.getJSONObject("first").put("cert", vector.getString("other_certificate")));
        for (String key : new String[]{"v", "epoch", "at_ms", "recover_delay_days"}) invalid(c -> c.put(key, 0));
        for (String key : new String[]{"v", "epoch", "at_ms", "recover_delay_days"}) invalid(c -> c.put(key, "3"));
        invalid(c -> c.put("extra", 1));
        invalid(c -> c.getJSONObject("first").put("extra", 1));
        invalid(c -> c.put("policy", "unknown"));
        invalid(c -> c.put("mode", "members"));
        invalid(c -> c.put("succession", new JSONObject()));
        invalid(c -> c.put("from", new JSONObject()));
        invalid(c -> c.getJSONObject("thresholds").put("routine", 16));
        invalid(c -> c.getJSONObject("thresholds").put("unit", "unknown"));
        invalid(c -> c.getJSONObject("holds").put("dispute_d", 31));
        invalid(c -> c.getJSONArray("admins").getJSONObject(0).put("since", 2));
        invalid(c -> c.getJSONArray("admins").getJSONObject(0).put("role", "unknown"));
        invalid(c -> c.getJSONArray("admins").getJSONObject(0).put("strength", "unknown"));
        invalid(c -> c.getJSONArray("admins").getJSONObject(0).put("key", "04" + "00".repeat(64)));
        for (String unit : new String[]{"person", "key"}) {
            for (boolean samePerson : new boolean[]{true, false}) {
                JSONObject c = charter();
                c.getJSONObject("thresholds").put("unit", unit).put("sensitive", 0);
                JSONArray admins = c.getJSONArray("admins");
                String person = "shared-person";
                for (int i = 0; i < admins.length(); i++) {
                    JSONObject k = admins.getJSONObject(i);
                    if (k.getString("role").equals("admin")) {
                        k.put("strength", "strong");
                        if (samePerson) k.put("person", person);
                    }
                }
                FoundingCharter automatic = recomputed(c, selected());
                if (!automatic.card.contains("Sensitive threshold: " + (samePerson ? 1 : 2) + " (automatic:")) throw new AssertionError(automatic.card);
                checks++;
            }
        }
        JSONArray keys = vector.getJSONArray("ed25519_keys");
        for (int i = 0; i < keys.length(); i++) {
            JSONObject candidate = keys.getJSONObject(i), c = charter();
            JSONObject admin = new JSONObject(c.getJSONArray("admins").getJSONObject(0).toString());
            admin.put("key", candidate.getString("key")).put("alg", "ed25519").put("role", "admin").put("strength", "weak");
            c.put("admins", new JSONArray().put(admin));
            if (candidate.getBoolean("valid")) { recomputed(c, candidate.getString("key")); checks++; }
            else refused(() -> recomputed(c, candidate.getString("key")));
        }
        java.math.BigInteger order = new java.math.BigInteger("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551",16);
        java.math.BigInteger half = order.shiftRight(1);
        equal(Hex.of(der(java.math.BigInteger.ONE, java.math.BigInteger.ONE)), Hex.of(P256Signature.normalize(der(java.math.BigInteger.ONE, order.subtract(java.math.BigInteger.ONE)))));
        equal(Hex.of(der(java.math.BigInteger.ONE, half)), Hex.of(P256Signature.normalize(der(java.math.BigInteger.ONE, half))));
        equal(Hex.of(der(java.math.BigInteger.ONE, half)), Hex.of(P256Signature.normalize(der(java.math.BigInteger.ONE, half.add(java.math.BigInteger.ONE)))));
        refused(() -> P256Signature.normalize(der(order, java.math.BigInteger.ONE)));
        refused(() -> P256Signature.normalize(der(java.math.BigInteger.ONE, order)));
        System.out.println("Phone founding protocol: " + checks + " checks passed");
    }

    private static byte[] der(java.math.BigInteger r, java.math.BigInteger s) {
        byte[] a = r.toByteArray(), b = s.toByteArray();
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        out.write(0x30); out.write(a.length + b.length + 4);
        out.write(2); out.write(a.length); out.write(a, 0, a.length);
        out.write(2); out.write(b.length); out.write(b, 0, b.length);
        return out.toByteArray();
    }
    private static void invalid(Consumer<JSONObject> mutation) throws Exception {
        JSONObject c = charter(); mutation.accept(c); refused(() -> recomputed(c, selected()));
    }
    private static FoundingCharter recomputed(JSONObject c, String key) throws Exception {
        JSONObject bare = new JSONObject(c.toString()), selected = null;
        JSONArray admins = bare.getJSONArray("admins");
        for (int i = 0; i < admins.length(); i++) {
            JSONObject k = admins.getJSONObject(i); k.put("acceptance", "");
            if (k.getString("key").equals(key)) selected = k;
        }
        if (selected == null) selected = admins.getJSONObject(0);
        String anchor = Hex.sha256(Canonical.of(bare));
        JSONObject body = new JSONObject().put("v", 2).put("anchor", anchor).put("prev", "");
        for (String field : new String[]{"key", "alg", "role", "strength", "person", "since"}) body.put(field, selected.get(field));
        byte[] bytes = ("poolhouse/pool-accept/v2\n" + Canonical.of(body)).getBytes(java.nio.charset.StandardCharsets.UTF_8);
        return new FoundingCharter(c, key, anchor, anchor.substring(0, 32), bytes);
    }
    private static JSONObject charter() { return new JSONObject(vector.getJSONObject("charter").toString()); }
    private static byte[] signing() { return Hex.parse(vector.getString("signing_bytes")); }
    private static String selected() { return vector.getString("selected_key"); }
    private static FoundingCharter read(JSONObject source) throws Exception {
        return new FoundingCharter(source, selected(), vector.getString("anchor"), vector.getString("pool_id"), signing());
    }
    private static void changed(Consumer<JSONObject> mutation) throws Exception {
        JSONObject c = charter(); mutation.accept(c); refused(() -> read(c));
    }
    private static void refused(Throwing task) throws Exception {
        try { task.run(); } catch (IllegalArgumentException | SecurityException | java.security.cert.CertificateException expected) { checks++; return; }
        throw new AssertionError("Malformed input was accepted");
    }
    private static void equal(Object expected, Object actual) {
        if (!expected.equals(actual)) throw new AssertionError(expected + " != " + actual);
        checks++;
    }
}
