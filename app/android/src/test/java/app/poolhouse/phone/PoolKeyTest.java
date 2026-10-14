package app.poolhouse.phone;

import org.json.JSONObject;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.Arrays;

final class PoolKeyTest {
    private static int checks;
    interface Throwing { void run() throws Exception; }
    public static void main(String[] args) throws Exception {
        JSONObject vector = new JSONObject(Files.readString(Path.of(args[0])));
        JSONObject source = vector.getJSONObject("charter");
        byte[] point = Hex.parse(vector.getString("selected_key"));
        String device = Hex.sha256(Hex.parse(source.getJSONObject("first").getString("cert")));
        String anchor = vector.getString("anchor"), pool = vector.getString("pool_id");
        byte[] bytes = Hex.parse(vector.getString("signing_bytes"));
        PoolKeyBinding.Request request = PoolKeyBinding.verify(source, point, device, anchor, pool, bytes, "");
        equal(anchor, request.anchor);
        equal(pool, request.poolId);
        equal(Hex.of(point), request.publicKey);
        equal(Hex.of(bytes), Hex.of(request.bytes()));
        equal(anchor, PoolKeyBinding.verify(source, point, device, anchor, pool, bytes, anchor).anchor);
        refused(() -> PoolKeyBinding.verify(source, point, "00".repeat(32), anchor, pool, bytes, ""));
        refused(() -> PoolKeyBinding.verify(source, point, device, anchor, pool, bytes, "00".repeat(32)));
        refused(() -> PoolKeyBinding.verify(source, new byte[65], device, anchor, pool, bytes, ""));
        refused(() -> PoolKeyBinding.verify(source, point, device, "00".repeat(32), pool, bytes, ""));
        refused(() -> PoolKeyBinding.verify(source, point, device, anchor, "00".repeat(16), bytes, ""));
        byte[] changed = bytes.clone(); changed[0] ^= 1;
        refused(() -> PoolKeyBinding.verify(source, point, device, anchor, pool, changed, ""));
        equal("poolhouse-pool-" + "ab".repeat(16), PoolKeyBinding.alias("ab".repeat(16)));
        for (String handle : new String[]{"", "poolhouse-phone-approval", "../key", "AB".repeat(16), "ab".repeat(15), "ab".repeat(17)}) refused(() -> PoolKeyBinding.alias(handle));
        refused(() -> PoolKeyBinding.alias(null));
        refused(() -> PoolKeyBinding.requireAnchor("", "not an anchor"));
        refused(() -> PoolKeyBinding.requireAnchor(null, anchor));
        JSONObject weak = new JSONObject(source.toString());
        weak.getJSONArray("admins").getJSONObject(0).put("strength", "weak");
        refused(() -> recomputed(weak, point, device));
        JSONObject recovery = new JSONObject(source.toString());
        recovery.getJSONArray("admins").getJSONObject(0).put("role", "recovery");
        recovery.getJSONArray("admins").getJSONObject(2).put("role", "admin");
        refused(() -> recomputed(recovery, point, device));
        byte[] returned = request.bytes(); returned[0] ^= 1;
        equal(Hex.of(bytes), Hex.of(request.bytes()));
        String card = request.card;
        source.put("policy", "open");
        equal(card, request.card);
        equal(Hex.of(bytes), Hex.of(request.bytes()));
        refused(() -> PoolKeyBinding.verify(source, point, device, anchor, pool, bytes, ""));
        KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
        generator.initialize(new ECGenParameterSpec("secp256r1"));
        KeyPair provider = generator.generateKeyPair();
        Signature signer = Signature.getInstance("SHA256withECDSA");
        signer.initSign(provider.getPrivate()); signer.update(request.bytes());
        byte[] low = P256Signature.normalize(signer.sign());
        Signature verifier = Signature.getInstance("SHA256withECDSA");
        verifier.initVerify(provider.getPublic()); verifier.update(request.bytes());
        if (!verifier.verify(low)) throw new AssertionError("normalized provider signature failed");
        checks++;
        if (!Arrays.equals(low, P256Signature.normalize(low))) throw new AssertionError("normalization is not idempotent");
        checks++;
        System.out.println("pool key binding: " + checks + " checks passed (software signature provider only)");
    }
    private static PoolKeyBinding.Request recomputed(JSONObject source, byte[] point, String device) throws Exception {
        JSONObject bare = new JSONObject(source.toString());
        for (int i = 0; i < bare.getJSONArray("admins").length(); i++) bare.getJSONArray("admins").getJSONObject(i).put("acceptance", "");
        String anchor = Hex.sha256(Canonical.of(bare));
        JSONObject selected = source.getJSONArray("admins").getJSONObject(0);
        JSONObject acceptance = new JSONObject().put("v", 2).put("anchor", anchor).put("prev", "");
        for (String key : new String[]{"key", "alg", "role", "strength", "person", "since"}) acceptance.put(key, selected.get(key));
        byte[] bytes = ("poolhouse/pool-accept/v2\n" + Canonical.of(acceptance)).getBytes(java.nio.charset.StandardCharsets.UTF_8);
        // Ensure the base protocol accepts these otherwise-valid terms; this leaf must refuse the selected role/strength.
        new FoundingCharter(source, Hex.of(point), anchor, anchor.substring(0,32), bytes);
        return PoolKeyBinding.verify(source, point, device, anchor, anchor.substring(0,32), bytes, "");
    }
    private static void equal(Object expected, Object actual) {
        if (!expected.equals(actual)) throw new AssertionError("mismatch");
        checks++;
    }
    private static void refused(Throwing run) throws Exception {
        try { run.run(); } catch (SecurityException | IllegalArgumentException expected) { checks++; return; }
        throw new AssertionError("accepted invalid pool key binding");
    }
}
