package app.poolhouse.phone;

import org.json.JSONObject;

/** Locally checked founding terms; transport prose or arbitrary bytes never become a signing request. */
final class PoolKeyBinding {
    private PoolKeyBinding() { }

    static final class Request {
        final String anchor, poolId, card, publicKey, deviceFingerprint;
        private final byte[] bytes;
        private Request(FoundingCharter charter, String publicKey, String deviceFingerprint) {
            this.anchor = charter.anchor;
            this.poolId = charter.poolId;
            this.card = charter.card;
            this.publicKey = publicKey;
            this.deviceFingerprint = deviceFingerprint;
            this.bytes = charter.bytes();
        }
        byte[] bytes() { return bytes.clone(); }
    }

    static Request verify(JSONObject source, byte[] localPoint, String deviceFingerprint,
                          String suppliedAnchor, String suppliedPool, byte[] suppliedBytes, String pinnedAnchor) throws Exception {
        if (!deviceFingerprint.matches("[0-9a-f]{64}")) throw bad();
        JSONObject snapshot = new JSONObject(source.toString());
        String publicKey = Hex.of(localPoint);
        if (!publicKey.matches("04[0-9a-f]{128}")) throw bad();
        FoundingCharter charter = new FoundingCharter(snapshot, publicKey, suppliedAnchor, suppliedPool, suppliedBytes);
        if (!Hex.sha256(Hex.parse(snapshot.getJSONObject("first").getString("cert"))).equals(deviceFingerprint)) throw bad();
        org.json.JSONArray admins = snapshot.getJSONArray("admins");
        for (int i = 0; i < admins.length(); i++) {
            JSONObject selected = admins.getJSONObject(i);
            if (publicKey.equals(selected.getString("key")) && (!selected.getString("alg").equals("p256")
                    || !selected.getString("role").equals("admin") || !selected.getString("strength").equals("strong"))) throw bad();
        }
        requireAnchor(pinnedAnchor, charter.anchor);
        return new Request(charter, publicKey, deviceFingerprint);
    }

    static String alias(String handle) {
        if (handle == null || !handle.matches("[0-9a-f]{32}")) throw bad();
        return "poolhouse-pool-" + handle;
    }

    static void requireAnchor(String pinned, String wanted) {
        if (wanted == null || !wanted.matches("[0-9a-f]{64}") || pinned == null || (!pinned.isEmpty() && !pinned.equals(wanted))) throw bad();
    }

    private static SecurityException bad() {
        return new SecurityException("This pool key does not match that founding request.");
    }
}
