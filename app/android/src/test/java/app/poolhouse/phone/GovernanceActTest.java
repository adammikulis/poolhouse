package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.function.Consumer;

final class GovernanceActTest {
    private static JSONObject foundingVector, vectors;
    private static FoundingCharter context;
    private static int checks;
    interface Throwing { void run() throws Exception; }

    public static void main(String[] args) throws Exception {
        foundingVector = new JSONObject(Files.readString(Path.of(args[0])));
        vectors = new JSONObject(Files.readString(Path.of(args[1])));
        context = new FoundingCharter(foundingVector.getJSONObject("charter"), key(), foundingVector.getString("anchor"), foundingVector.getString("pool_id"), Hex.parse(foundingVector.getString("signing_bytes")));
        JSONArray rows = vectors.getJSONArray("rows");
        for (int i = 0; i < rows.length(); i++) {
            JSONObject row = rows.getJSONObject(i), act = new JSONObject(row.getJSONObject("act").toString());
            GovernanceAct result = read(act, bytes(act), key(), now());
            equal(row.getString("bytes"), Hex.of(result.bytes()));
            equal(row.getString("id"), result.id);
            contains(result.card, "Policy: secure");
            contains(result.card, Canonical.of(act.getJSONObject("body")));
            byte[] returned = result.bytes(); returned[0] = 0;
            act.getJSONObject("body").put("summary", "changed");
            equal(row.getString("bytes"), Hex.of(result.bytes()));
        }
        JSONObject admit = act(0), remove = act(1);
        GovernanceAct admission = read(admit, bytes(admit), key(), now());
        contains(admission.card, FoundingCharter.certificateWords(admit.getJSONObject("body").getString("cert")));
        GovernanceAct removal = read(remove, bytes(remove), key(), now());
        contains(removal.card, Hex.sha256(Canonical.of(remove.getJSONObject("body").getJSONArray("cut"))));
        contains(removal.card, "through entry 3");
        contains(removal.card, "through entry 0");
        changed(0, a -> a.put("pool", "00".repeat(16)));
        changed(0, a -> a.put("prev", "00".repeat(32)));
        changed(0, a -> a.put("epoch", 2));
        for (Object version : new Object[]{1,3,"2",2.0,JSONObject.NULL}) changed(0, a -> a.put("v", version));
        for (String kind : new String[]{"found","admin_add","delegate","unknown"}) changed(0, a -> a.put("kind", kind));
        changed(0, a -> a.put("summary", "misleading"));
        changed(0, a -> a.put("nonce", "0"));
        changed(0, a -> a.put("signatures", new JSONArray().put(new JSONObject())));
        changed(0, a -> a.put("at_ms", now() + 1));
        changed(0, a -> a.put("at_ms", "1"));
        changed(0, a -> a.put("not_after", now()));
        changed(0, a -> a.put("not_after", Long.MAX_VALUE));
        changed(0, a -> a.put("not_after", a.getLong("at_ms") + 3600001));
        changed(0, a -> a.getJSONObject("body").put("name", "caf\u00e9"));
        changed(0, a -> a.getJSONObject("body").put("name", "line\nbreak"));
        changed(0, a -> a.getJSONObject("body").put("cert", "3000"));
        changed(0, a -> a.getJSONObject("body").put("cert", a.getJSONObject("body").getString("cert") + "00"));
        changed(0, a -> a.getJSONObject("body").put("how", "found"));
        changed(0, a -> a.getJSONObject("body").put("via", "internet"));
        changed(0, a -> a.getJSONObject("body").put("summary", "join safely"));
        changed(1, a -> a.getJSONObject("body").put("fingerprint", "00"));
        changed(1, a -> a.getJSONObject("body").getJSONArray("cut").getJSONObject(0).put("seq", -1));
        changed(1, a -> a.getJSONObject("body").getJSONArray("cut").getJSONObject(0).put("hash", ""));
        changed(1, a -> a.getJSONObject("body").getJSONArray("cut").getJSONObject(0).put("board", "a..b"));
        changed(1, a -> a.getJSONObject("body").getJSONArray("cut").getJSONObject(0).put("origin", "bad"));
        for (String board : new String[]{"*", "all", "everyone", "workspace", "system", "human", "owner-token"}) changed(1, a -> a.getJSONObject("body").getJSONArray("cut").getJSONObject(0).put("board", board));
        changed(1, a -> a.getJSONObject("body").getJSONArray("cut").getJSONObject(0).put("summary", "keep everything"));
        refused(() -> read(admit, bytes(admit), "00", now()));
        JSONArray admins = foundingVector.getJSONObject("charter").getJSONArray("admins");
        for (int i = 0; i < admins.length(); i++) {
            String selected = admins.getJSONObject(i).getString("key");
            if (!selected.equals(key())) refused(() -> read(admit, bytes(admit), selected, now()));
        }
        refused(() -> read(admit, bytes(admit), key(), 0));
        refused(() -> read(admit, bytes(admit), key(), Long.MAX_VALUE));
        for (int index = 0; index < 2; index++) {
            JSONObject altered = act(index);
            altered.getJSONObject("body").put(index == 0 ? "cert" : "fingerprint", index == 0 ? foundingVector.getJSONObject("charter").getJSONObject("first").getString("cert") : "cd".repeat(32));
            final int current = index;
            refused(() -> read(altered, bytes(act(current)), key(), now()));
        }
        JSONObject isolated = context.terms(); isolated.put("policy", "open");
        contains(read(admit, bytes(admit), key(), now()).card, "Policy: secure");
        changed(0, a -> a.put("at_ms", Long.MAX_VALUE));
        changed(0, a -> a.put("not_after", -1));
        changed(1, a -> { JSONArray cut = a.getJSONObject("body").getJSONArray("cut"); while (cut.length() < 257) cut.put(cut.getJSONObject(0)); });
        byte[] wrongDomain = bytes(admit); wrongDomain[0] = 'x';
        refused(() -> read(admit, wrongDomain, key(), now()));
        System.out.println("Governance act protocol checks: " + checks);
    }

    private static void changed(int index, Consumer<JSONObject> mutate) throws Exception {
        JSONObject value = act(index); mutate.accept(value);
        refused(() -> read(value, bytes(value), key(), now()));
    }
    private static JSONObject act(int index) { return new JSONObject(vectors.getJSONArray("rows").getJSONObject(index).getJSONObject("act").toString()); }
    private static String key() { return foundingVector.getString("selected_key"); }
    private static long now() { return vectors.getLong("now_ms"); }
    private static byte[] bytes(JSONObject source) { JSONObject bare = new JSONObject(source.toString()); bare.remove("signatures"); return ("poolhouse/pool-act/v2\n" + Canonical.of(bare)).getBytes(StandardCharsets.UTF_8); }
    private static GovernanceAct read(JSONObject act, byte[] bytes, String key, long now) throws Exception { return new GovernanceAct(context, act, key, bytes, now); }
    private static void contains(String value, String part) { checks++; if (!value.contains(part)) throw new AssertionError(part); }
    private static void equal(Object expected, Object actual) { checks++; if (!expected.equals(actual)) throw new AssertionError(expected + " != " + actual); }
    private static void refused(Throwing task) throws Exception { checks++; try { task.run(); } catch (IllegalArgumentException | SecurityException | org.json.JSONException | java.security.cert.CertificateException expected) { return; } throw new AssertionError("Accepted hostile act"); }
}
